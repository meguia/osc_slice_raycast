"""
Minimal OSC slice raycast server for Blender.
Incoming:
    /slice/set object_index samples normal_x normal_y normal_z distance
Outgoing:
    /slice/radii object_index and the list of samples radii
"""

from __future__ import annotations
import math
import threading
from typing import Iterable

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


COLLECTION_NAME = "Objects" # it assumes that is the name of the collection with the objects to slice in the blend file
OSC_RECEIVE_HOST = "0.0.0.0"
OSC_RECEIVE_PORT = 9000
OSC_SEND_HOST = "127.0.0.1"
OSC_SEND_PORT = 9001
RAY_EPSILON = 1.0e-7 

client = OSCClient(OSC_SEND_HOST, OSC_SEND_PORT)
server = OSCThreadServer()
mesh_caches: dict[str, dict[str, object]] = {}

def set_slice(*args: object) -> None:
    if len(args) != 6:
        raise ValueError(
            "/slice/set expects object_index samples nx ny nz distance"
        )

    object_index = as_int(args[0])
    sample_count = as_int(args[1])
    obj = object_by_index(object_index)
    normal = normal_vector(args[2:5])
    distance = as_float(args[5])
    bvh = bvh_for_object(obj)
    radii = sample_radii(bvh, normal, distance, sample_count)
    client.send_message(b"/slice/radii", [object_index, *radii])


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


def sample_radii(bvh: BVHTree, normal: Vector, distance: float, sample_count: int) -> list[float]:
    basis_u, basis_v = slice_plane_basis(normal)
    center = normal * distance
    radii = []

    for sample_index in range(sample_count):
        angle = math.tau * sample_index / sample_count
        direction = basis_u * math.cos(angle) + basis_v * math.sin(angle)
        radii.append(ray_radius(bvh, center, direction))

    return radii


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
    collection = bpy.data.collections.get(COLLECTION_NAME)
    objects = list(collection.objects) if collection else []
    if index < 0 or index >= len(objects):
        raise IndexError(f"object index {index} is out of range")
    return objects[index]


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


server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
server.bind(b"/slice/set", set_slice)
print(f"OSC slice v1 listening on {OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}")

if __name__ == "__main__":
    threading.Event().wait()
