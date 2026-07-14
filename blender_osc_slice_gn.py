"""
Fast BVH slice sampler for Blender, with a Geometry Nodes carrier object.

Incoming OSC:
    /slice/set object_index radial_sample_count normal_x normal_y normal_z distance
    /slice/clear
    /slice/clear object_index
    /slice/cache/clear

Outgoing OSC:
    /slice/radii object_index radius_0 ... radius_N_minus_1
    /osc/error message

"""

from __future__ import annotations

import math
import queue
import time
from typing import Iterable

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


# Configuration

COLLECTION_NAME = "Objects"
SLICE_COLLECTION_NAME = "Slices"
CARRIER_OBJECT_PREFIX = "SliceGN-"

OSC_RECEIVE_HOST = "0.0.0.0"
OSC_RECEIVE_PORT = 9000
OSC_SEND_HOST = "127.0.0.1"
OSC_SEND_PORT = 9001

UPDATE_INTERVAL_SECONDS = 1.0 / 250.0 # interval for checking OSC messages
RAY_EPSILON = 1.0e-7 # precision for BVH ray hits
CACHE_STATIC_MESHES = True # avoids recomputing the BVH every message


# Runtime state

_server: OSCThreadServer | None = None
_client: OSCClient | None = None
_incoming: "queue.Queue[tuple[str, tuple[object, ...]]]" = queue.Queue()
_timer_running = False


# Each cached mesh is a dictionary with: signature, bvh, triangle_count, build_ms.
_mesh_caches: dict[str, dict[str, object]] = {}


# OSC callbacks run on oscpy thread


def _enqueue_set_slice(*args: object) -> None:
    _incoming.put(("set_slice", args))


def _enqueue_clear_slice(*args: object) -> None:
    _incoming.put(("clear_slice", args))


def _enqueue_clear_cache(*args: object) -> None:
    _incoming.put(("clear_cache", args))


# Public


def start_osc_slice_gn() -> None:
    global _client, _server, _timer_running

    bpy.app.driver_namespace["osc_slice_gn_start"] = start_osc_slice_gn
    bpy.app.driver_namespace["osc_slice_gn_stop"] = stop_osc_slice_gn

    if _client is None:
        _client = OSCClient(OSC_SEND_HOST, OSC_SEND_PORT)

    if _server is None:
        _server = OSCThreadServer()
        _server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
        _server.bind(b"/slice/set", _enqueue_set_slice)
        _server.bind(b"/slice/clear", _enqueue_clear_slice)
        _server.bind(b"/slice/cache/clear", _enqueue_clear_cache)

    if not _timer_running:
        bpy.app.timers.register(_process_osc, first_interval=0.0)
        _timer_running = True

    print(
        "OSC slice GN started: "
        f"receiving {OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}, "
        f"sending {OSC_SEND_HOST}:{OSC_SEND_PORT}"
    )


def stop_osc_slice_gn() -> None:
    global _client, _server, _timer_running

    if _server is not None:
        _server.stop_all()
        _server = None

    _client = None
    _timer_running = False
    _mesh_caches.clear()
    bpy.app.driver_namespace.pop("osc_slice_gn_start", None)
    bpy.app.driver_namespace.pop("osc_slice_gn_stop", None)
    print("OSC slice GN stopped")


# Main-thread message processing


def _process_osc() -> float | None:
    global _timer_running

    while True:
        try:
            command, args = _incoming.get_nowait()
        except queue.Empty:
            break

        try:
            _handle_command(command, args)
        except Exception as exc:
            _send_error(f"{command}: {exc}")
            print(f"OSC command error ({command}): {exc}")

    if _server is None:
        _timer_running = False
        return None

    return UPDATE_INTERVAL_SECONDS


def _handle_command(command: str, args: tuple[object, ...]) -> None:
    if command == "set_slice":
        _set_slice(args)
    elif command == "clear_slice":
        _clear_slice(args)
    elif command == "clear_cache":
        _mesh_caches.clear()
        print("OSC slice GN mesh cache cleared")
    else:
        raise ValueError(f"unknown command {command!r}")


def _set_slice(args: tuple[object, ...]) -> None:
    if len(args) != 6:
        raise ValueError(
            "/slice/set expects: "
            "object_index radial_sample_count normal_x normal_y normal_z distance"
        )

    total_started = time.perf_counter()
    object_index = _as_int(args[0])
    sample_count = _as_positive_int(args[1], "radial_sample_count")
    obj = _object_by_index(object_index)
    normal = _normal_vector(args[2:5], "normal")
    distance = _as_float(args[5])

    cache_started = time.perf_counter()
    cache, cache_hit = _bvh_cache_for_object(obj)
    cache_ms = (time.perf_counter() - cache_started) * 1000.0

    sample_started = time.perf_counter()
    radii, points = _sample_points(cache["bvh"], normal, distance, sample_count)
    sample_ms = (time.perf_counter() - sample_started) * 1000.0

    carrier_started = time.perf_counter()
    edge_count = _update_carrier_object(obj, radii, points)
    carrier_ms = (time.perf_counter() - carrier_started) * 1000.0

    compute_ms = (time.perf_counter() - total_started) * 1000.0

    osc_started = time.perf_counter()
    _send_message(b"/slice/radii", [object_index, *radii])
    osc_ms = (time.perf_counter() - osc_started) * 1000.0
    total_ms = compute_ms + osc_ms

    cache_label = "hit" if cache_hit else f"built in {cache['build_ms']:.2f} ms"
    print(
        f"BVH slice GN {obj.name!r}: {sample_count} samples, "
        f"{edge_count} edges, {cache['triangle_count']} triangles, "
        f"cache {cache_label}, cache lookup {cache_ms:.2f} ms, "
        f"rays {sample_ms:.2f} ms, carrier {carrier_ms:.2f} ms, "
        f"osc send {osc_ms:.2f} ms, total {total_ms:.2f} ms"
    )


def _clear_slice(args: tuple[object, ...]) -> None:
    if len(args) == 0:
        _remove_all_carrier_objects()
        return

    object_index = _as_int(args[0])
    obj = _object_by_index_or_none(object_index)
    if obj is not None:
        _remove_object_and_mesh(bpy.data.objects.get(_carrier_object_name(obj.name)))


# BVH cache and radial sampling


def _bvh_cache_for_object(obj: bpy.types.Object) -> tuple[dict[str, object], bool]:
    mesh = _mesh_for_object(obj)
    signature = (
        obj.name_full,
        mesh.name_full,
        len(mesh.vertices),
        len(mesh.edges),
        len(mesh.polygons),
    )

    cache = _mesh_caches.get(obj.name_full)
    if CACHE_STATIC_MESHES and cache is not None and cache["signature"] == signature:
        return cache, True

    started = time.perf_counter()
    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    if not vertices or not triangles:
        raise ValueError(f"{obj.name!r} has no triangles for BVH sampling")

    cache = {
        "signature": signature,
        "bvh": BVHTree.FromPolygons(vertices, triangles, all_triangles=True),
        "triangle_count": len(triangles),
        "build_ms": (time.perf_counter() - started) * 1000.0,
    }
    if CACHE_STATIC_MESHES:
        _mesh_caches[obj.name_full] = cache

    return cache, False


def _sample_points(
    bvh: BVHTree,
    normal: Vector,
    distance: float,
    sample_count: int,
) -> tuple[list[float], list[tuple[float, float, float] | None]]:
    # The BVH uses mesh vertex coordinates, so normal and distance are object-local.
    basis_u, basis_v = _slice_plane_basis(normal)
    center = normal * distance
    radii: list[float] = []
    points: list[tuple[float, float, float] | None] = []

    for sample_index in range(sample_count):
        angle = math.tau * sample_index / sample_count
        direction = basis_u * math.cos(angle) + basis_v * math.sin(angle)
        radius = _bvh_ray_radius(bvh, center, direction)
        radii.append(radius)

        point = center + direction * radius
        points.append((point.x, point.y, point.z) if radius > RAY_EPSILON else None)

    return radii, points


def _bvh_ray_radius(bvh: BVHTree, center: Vector, direction: Vector) -> float:
    hit = bvh.ray_cast(center, direction)
    if hit is None:
        return 0.0

    _position, _normal, _face_index, distance = hit
    if distance is None:
        return 0.0
    if distance > RAY_EPSILON:
        return float(distance)

    offset_hit = bvh.ray_cast(center + direction * RAY_EPSILON, direction)
    if offset_hit is None:
        return 0.0

    _position, _normal, _face_index, offset_distance = offset_hit
    return 0.0 if offset_distance is None else float(offset_distance + RAY_EPSILON)


def _slice_plane_basis(normal: Vector) -> tuple[Vector, Vector]:
    reference = Vector((1.0, 0.0, 0.0))
    if abs(normal.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))

    basis_u = reference - normal * normal.dot(reference)
    basis_u.normalize()
    basis_v = normal.cross(basis_u)
    basis_v.normalize()
    return basis_u, basis_v


# Geometry Nodes carrier object


def _update_carrier_object(
    source: bpy.types.Object,
    radii: list[float],
    points: list[tuple[float, float, float] | None],
) -> int:
    carrier = _carrier_object_for_source(source)
    mesh = carrier.data
    vertices: list[tuple[float, float, float]] = []
    sample_to_vertex: dict[int, int] = {}

    for sample_index, point in enumerate(points):
        if point is None:
            continue
        sample_to_vertex[sample_index] = len(vertices)
        vertices.append(point)

    edges: list[tuple[int, int]] = []
    for sample_index in range(len(points)):
        vertex_a = sample_to_vertex.get(sample_index)
        vertex_b = sample_to_vertex.get((sample_index + 1) % len(points))
        if vertex_a is not None and vertex_b is not None and vertex_a != vertex_b:
            edges.append((vertex_a, vertex_b))

    mesh.clear_geometry()
    mesh.from_pydata(vertices, edges, [])
    mesh.update()

    carrier.matrix_world = source.matrix_world.copy()
    carrier.hide_render = True
    return len(edges)


def _carrier_object_for_source(source: bpy.types.Object) -> bpy.types.Object:
    name = _carrier_object_name(source.name)
    existing = bpy.data.objects.get(name)
    if existing is not None:
        if existing.type != "MESH":
            raise TypeError(f"{name!r} exists but is not a mesh")
        return existing

    mesh = bpy.data.meshes.new(name + " Mesh")
    obj = bpy.data.objects.new(name, mesh)
    collection = bpy.data.collections.get(SLICE_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(SLICE_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    return obj


def _remove_all_carrier_objects() -> None:
    for obj in tuple(bpy.data.objects):
        if obj.name.startswith(CARRIER_OBJECT_PREFIX):
            _remove_object_and_mesh(obj)


def _remove_object_and_mesh(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return

    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _carrier_object_name(source_name: str) -> str:
    return CARRIER_OBJECT_PREFIX + source_name


# Small helpers


def _object_by_index(index: int) -> bpy.types.Object:
    obj = _object_by_index_or_none(index)
    if obj is None:
        count = len(_slice_objects())
        raise IndexError(f"object index {index} is out of range ({count} objects)")
    return obj


def _object_by_index_or_none(index: int) -> bpy.types.Object | None:
    objects = _slice_objects()
    return objects[index] if 0 <= index < len(objects) else None


def _slice_objects() -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        return []
    return sorted(
        (obj for obj in collection.objects if obj.type == "MESH"),
        key=lambda obj: obj.name.lower(),
    )


def _mesh_for_object(obj: bpy.types.Object) -> bpy.types.Mesh:
    if obj.type != "MESH":
        raise TypeError(f"{obj.name!r} is not a mesh object")
    return obj.data


def _normal_vector(values: Iterable[object], label: str) -> Vector:
    vector = Vector(tuple(_as_float(value) for value in values))
    if len(vector) != 3 or vector.length == 0.0:
        raise ValueError(f"{label} must be a non-zero vector with three components")
    vector.normalize()
    return vector


def _as_int(value: object) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)


def _as_positive_int(value: object, label: str) -> int:
    integer = _as_int(value)
    if integer <= 0:
        raise ValueError(f"{label} must be positive")
    return integer


def _as_float(value: object) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)


def _send_error(message: str) -> None:
    print(f"OSC error: {message}")
    _send_message(b"/osc/error", [message])


def _send_message(address: bytes, values: list[object]) -> None:
    if _client is None:
        return

    try:
        _client.send_message(address, values)
    except Exception as exc:
        print(f"OSC send failed for {address!r}: {exc}")


if __name__ == "__main__":
    start_osc_slice_gn()
