"""
Minimal OSC slice server for Blender with fast Geometry Nodes visual cutting.

Incoming:
    /slice/set object_index samples normal_x normal_y normal_z distance
Outgoing:
    /slice/radii object_index and the list of samples radii

The BVH raycast still sends radii for SuperCollider. The visual cut is done by a
Geometry Nodes modifier on the source object: delete faces where
    dot(Position, Slice Normal) > Slice Distance
No SliceGN carrier ring is created. A lightweight SliceCap mesh is updated as a cosmetic filled cut face.
"""

from __future__ import annotations

import math
import queue
from typing import Iterable

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


COLLECTION_NAME = "Objects"
SLICE_NODE_GROUP_NAME = "SliceHalf"
SLICE_MODIFIER_NAME = "SliceHalf"
CARRIER_OBJECT_PREFIX = "SliceGN-"
CAP_OBJECT_PREFIX = "SliceCap-"
CAP_COLLECTION_NAME = "Slice Caps"
CAP_MATERIAL_NAME = "Slice Cap Material"
SHOW_SLICE_CAPS = True
HIDE_OLD_CARRIER_RINGS = True
OSC_RECEIVE_HOST = "0.0.0.0"
OSC_RECEIVE_PORT = 9000
OSC_SEND_HOST = "127.0.0.1"
OSC_SEND_PORT = 9001
RAY_EPSILON = 1.0e-7
UPDATE_INTERVAL_SECONDS = 1.0 / 250.0

client = OSCClient(OSC_SEND_HOST, OSC_SEND_PORT)
server = OSCThreadServer()
incoming: "queue.Queue[tuple[object, ...]]" = queue.Queue()
mesh_caches: dict[str, dict[str, object]] = {}


def enqueue_set_slice(*args: object) -> None:
    incoming.put(args)


def process_osc() -> float:
    while True:
        try:
            args = incoming.get_nowait()
        except queue.Empty:
            break

        try:
            set_slice(*args)
        except Exception as exc:
            print(f"OSC visual v2 error: {exc}")

    return UPDATE_INTERVAL_SECONDS


def set_slice(*args: object) -> None:
    if len(args) != 6:
        raise ValueError("/slice/set expects object_index samples nx ny nz distance")

    object_index = as_int(args[0])
    sample_count = as_int(args[1])
    obj = object_by_index(object_index)
    normal = normal_vector(args[2:5])
    distance = as_float(args[5])

    update_slice_modifier(obj, normal, distance)

    bvh = bvh_for_object(obj)
    radii, points = sample_slice(bvh, normal, distance, sample_count)
    update_cap_object(obj, normal, distance, points)
    client.send_message(b"/slice/radii", [object_index, *radii])


def ensure_slice_node_group() -> bpy.types.GeometryNodeTree:
    existing = bpy.data.node_groups.get(SLICE_NODE_GROUP_NAME)
    if existing is not None:
        return existing

    group = bpy.data.node_groups.new(SLICE_NODE_GROUP_NAME, "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Slice Normal", in_out="INPUT", socket_type="NodeSocketVector")
    group.interface.new_socket(name="Slice Distance", in_out="INPUT", socket_type="NodeSocketFloat")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nodes = group.nodes
    links = group.links
    group_input = nodes.new("NodeGroupInput")
    group_output = nodes.new("NodeGroupOutput")
    position = nodes.new("GeometryNodeInputPosition")
    dot = nodes.new("ShaderNodeVectorMath")
    compare = nodes.new("FunctionNodeCompare")
    delete = nodes.new("GeometryNodeDeleteGeometry")

    group_input.location = (-600, 0)
    position.location = (-600, -220)
    dot.location = (-380, -120)
    compare.location = (-160, -120)
    delete.location = (80, 0)
    group_output.location = (320, 0)

    dot.operation = "DOT_PRODUCT"
    compare.data_type = "FLOAT"
    compare.operation = "GREATER_THAN"
    delete.domain = "FACE"
    delete.mode = "ALL"

    links.new(group_input.outputs["Geometry"], delete.inputs["Geometry"])
    links.new(position.outputs["Position"], dot.inputs[0])
    links.new(group_input.outputs["Slice Normal"], dot.inputs[1])
    links.new(dot.outputs["Value"], compare.inputs["A"])
    links.new(group_input.outputs["Slice Distance"], compare.inputs["B"])
    links.new(compare.outputs["Result"], delete.inputs["Selection"])
    links.new(delete.outputs["Geometry"], group_output.inputs["Geometry"])

    return group


def ensure_slice_modifier(obj: bpy.types.Object) -> bpy.types.NodesModifier:
    group = ensure_slice_node_group()
    modifier = obj.modifiers.get(SLICE_MODIFIER_NAME)
    if modifier is None:
        modifier = obj.modifiers.new(SLICE_MODIFIER_NAME, "NODES")
    modifier.node_group = group
    return modifier


def update_slice_modifier(obj: bpy.types.Object, normal: Vector, distance: float) -> None:
    modifier = ensure_slice_modifier(obj)
    group = modifier.node_group
    normal_key = socket_identifier(group, "Slice Normal")
    distance_key = socket_identifier(group, "Slice Distance")
    modifier[normal_key] = (normal.x, normal.y, normal.z)
    modifier[distance_key] = distance
    obj.update_tag()


def socket_identifier(group: bpy.types.GeometryNodeTree, name: str) -> str:
    for item in group.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item.identifier
    raise KeyError(f'{group.name!r} has no input socket named {name!r}')


def ensure_all_slice_modifiers() -> None:
    for obj in slice_objects():
        ensure_slice_modifier(obj)



def hide_old_carrier_rings() -> None:
    if not HIDE_OLD_CARRIER_RINGS:
        return

    for obj in bpy.data.objects:
        if obj.name.startswith(CARRIER_OBJECT_PREFIX):
            obj.hide_viewport = True
            obj.hide_render = True
def bvh_for_object(obj: bpy.types.Object) -> BVHTree:
    mesh = mesh_for_object(obj)
    signature = (
        obj.name_full,
        mesh.name_full,
        len(mesh.vertices),
        len(mesh.edges),
        len(mesh.polygons),
    )
    cached = mesh_caches.get(obj.name_full)
    if cached is not None and cached["signature"] == signature:
        return cached["bvh"]

    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    if not vertices or not triangles:
        raise ValueError(f"{obj.name!r} has no triangles")

    bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    mesh_caches[obj.name_full] = {"signature": signature, "bvh": bvh}
    return bvh


def sample_slice(
    bvh: BVHTree,
    normal: Vector,
    distance: float,
    sample_count: int,
) -> tuple[list[float], list[tuple[float, float, float] | None]]:
    basis_u, basis_v = slice_plane_basis(normal)
    center = normal * distance
    radii = []
    points = []

    for sample_index in range(sample_count):
        angle = math.tau * sample_index / sample_count
        direction = basis_u * math.cos(angle) + basis_v * math.sin(angle)
        radius = ray_radius(bvh, center, direction)
        point = center + direction * radius
        radii.append(radius)
        points.append((point.x, point.y, point.z) if radius > RAY_EPSILON else None)

    return radii, points



def update_cap_object(
    source: bpy.types.Object,
    normal: Vector,
    distance: float,
    points: list[tuple[float, float, float] | None],
) -> None:
    if not SHOW_SLICE_CAPS:
        return

    cap = cap_object_for_source(source)
    center = normal * distance
    vertices = [(center.x, center.y, center.z)]
    ring_indices = []

    for point in points:
        if point is None:
            ring_indices.append(None)
            continue
        ring_indices.append(len(vertices))
        vertices.append(point)

    faces = []
    for index, vertex_a in enumerate(ring_indices):
        vertex_b = ring_indices[(index + 1) % len(ring_indices)]
        if vertex_a is not None and vertex_b is not None and vertex_a != vertex_b:
            faces.append((0, vertex_a, vertex_b))

    cap.data.clear_geometry()
    cap.data.from_pydata(vertices, [], faces)
    cap.data.update()
    cap.matrix_world = source.matrix_world.copy()
    cap.hide_viewport = False
    cap.hide_render = False

    material = cap_material()
    if not cap.data.materials:
        cap.data.materials.append(material)
    else:
        cap.data.materials[0] = material


def cap_object_for_source(source: bpy.types.Object) -> bpy.types.Object:
    name = CAP_OBJECT_PREFIX + source.name
    existing = bpy.data.objects.get(name)
    if existing is not None:
        if existing.type != "MESH":
            raise TypeError(f"{name!r} exists but is not a mesh")
        return existing

    mesh = bpy.data.meshes.new(name + " Mesh")
    obj = bpy.data.objects.new(name, mesh)
    collection = bpy.data.collections.get(CAP_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(CAP_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    return obj


def cap_material() -> bpy.types.Material:
    material = bpy.data.materials.get(CAP_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(CAP_MATERIAL_NAME)
        material.diffuse_color = (0.9, 0.18, 0.08, 1.0)
    return material

def ray_radius(bvh: BVHTree, center: Vector, direction: Vector) -> float:
    hit = bvh.ray_cast(center, direction)
    if hit is None:
        return 0.0

    _position, _normal, _face_index, distance = hit
    if distance is None:
        return 0.0
    if distance > RAY_EPSILON:
        return float(distance)

    hit = bvh.ray_cast(center + direction * RAY_EPSILON, direction)
    if hit is None:
        return 0.0

    _position, _normal, _face_index, distance = hit
    return 0.0 if distance is None else float(distance + RAY_EPSILON)


def slice_plane_basis(normal: Vector) -> tuple[Vector, Vector]:
    reference = Vector((1.0, 0.0, 0.0))
    if abs(normal.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))

    basis_u = reference - normal * normal.dot(reference)
    basis_u.normalize()
    basis_v = normal.cross(basis_u)
    basis_v.normalize()
    return basis_u, basis_v


def object_by_index(index: int) -> bpy.types.Object:
    objects = slice_objects()
    if index < 0 or index >= len(objects):
        raise IndexError(f"object index {index} is out of range")
    return objects[index]


def slice_objects() -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        return []
    return sorted(
        (obj for obj in collection.objects if obj.type == "MESH"),
        key=lambda obj: obj.name.lower(),
    )


def mesh_for_object(obj: bpy.types.Object) -> bpy.types.Mesh:
    if obj.type != "MESH":
        raise TypeError(f"{obj.name!r} is not a mesh object")
    return obj.data


def normal_vector(values: Iterable[object]) -> Vector:
    vector = Vector(tuple(as_float(value) for value in values))
    if len(vector) != 3 or vector.length == 0.0:
        raise ValueError("normal must be a non-zero vector with three components")
    vector.normalize()
    return vector


def as_int(value: object) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)


def as_float(value: object) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)


hide_old_carrier_rings()
ensure_all_slice_modifiers()
server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
server.bind(b"/slice/set", enqueue_set_slice)
bpy.app.timers.register(process_osc, first_interval=0.0)
print(f"OSC slice visual v2 listening on {OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}")