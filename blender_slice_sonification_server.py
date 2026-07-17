"""
Headless high-resolution OSC slice service for sonification.

Run with:
    blender -b slice_osc_collection.blend --python blender_slice_sonification_server.py

Incoming:
    /slice/get message_id object_id num_samples normal_x normal_y normal_z distance

Outgoing:
    /slice/radii message_id object_id radius_0 ... radius_N_minus_1
"""

from __future__ import annotations
import math
import queue
import threading
from dataclasses import dataclass
from typing import Iterable
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

SOURCE_COLLECTION_NAME = "Objects"  # High-resolution meshes.
OSC_RECEIVE_HOST = "0.0.0.0"  # Request bind host.
OSC_RECEIVE_PORT = 9000  # /slice/get port.
OSC_SEND_HOST = "127.0.0.1"  # Reply host.
OSC_SEND_PORT = 9001  # /slice/radii port.
RAY_EPSILON = 1.0e-7  # Avoid origin self-hits.

@dataclass(frozen=True)
class SliceRequest:
    """Queued sonification request."""
    message_id: int
    object_id: int
    sample_count: int
    normal: tuple[float, float, float]
    distance: float

_server: OSCThreadServer | None = None  # OSC listener.
_worker: threading.Thread | None = None  # FIFO slicer thread.
_requests: "queue.Queue[SliceRequest | None]" = queue.Queue()  # Work queue.
_bvhs: dict[int, BVHTree] = {}  # object_id -> BVH.

def start_sonification_server() -> None:
    """Start the background slice service."""
    global _server, _worker
    if _server is not None:
        print("Sonification slice server is already running")
        return

    for object_id, obj in _objects_by_id().items():
        _bvhs[object_id] = _build_bvh(obj)
    _worker = threading.Thread(
        target=_request_worker,
        args=(OSCClient(OSC_SEND_HOST, OSC_SEND_PORT),),
        name="slice-sonification-worker",
        daemon=True,
    )
    _worker.start()
    server = OSCThreadServer()
    server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
    server.bind(b"/slice/get", _enqueue_slice_get)
    _server = server
    print(
        f"Sonification slice server ready on {OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}; "
        f"sending replies to {OSC_SEND_HOST}:{OSC_SEND_PORT}"
    )

def stop_sonification_server() -> None:
    """Stop the listener and worker."""
    global _server, _worker
    if _server is not None:
        _server.stop_all()
        _server = None
    if _worker is not None:
        _requests.put(None)
        _worker.join(timeout=2.0)
        _worker = None
    print("Sonification slice server stopped")

def _enqueue_slice_get(*args: object) -> None:
    """Queue one /slice/get request."""
    try:
        _requests.put(
            SliceRequest(
                message_id=_as_int(args[0]),
                object_id=_as_int(args[1]),
                sample_count=_as_int(args[2]),
                normal=tuple(_as_float(value) for value in args[3:6]),
                distance=_as_float(args[6]),
            )
        )
    except Exception as exc:
        print(f"Invalid /slice/get: {exc}")

def _request_worker(client: OSCClient) -> None:
    """Process queued requests and send radii."""
    while True:
        request = _requests.get()
        if request is None:
            break
        try:
            normal = _normal_vector(request.normal)
            radii = _sample_radii(
                _bvhs[request.object_id],
                normal,
                request.distance,
                request.sample_count,
            )
            client.send_message(
                b"/slice/radii",
                [request.message_id, request.object_id, *radii],
            )
        except Exception as exc:
            print(
                f"Slice failed message={request.message_id} "
                f"object={request.object_id}: {exc}"
            )

def _objects_by_id() -> dict[int, bpy.types.Object]:
    """Map slice_index values to mesh objects."""
    return {
        int(obj["slice_index"]): obj
        for obj in bpy.data.collections[SOURCE_COLLECTION_NAME].all_objects
        if obj.type == "MESH"
    }

def _build_bvh(obj: bpy.types.Object) -> BVHTree:
    """Build a BVH from a mesh object."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    return BVHTree.FromPolygons(vertices, triangles, all_triangles=True)

def _sample_radii(bvh: BVHTree, normal: Vector, distance: float, sample_count: int) -> list[float]:
    """Sample ray radii around one slice plane."""
    basis_u, basis_v = _slice_plane_basis(normal)
    center = normal * distance
    return [
        _ray_radius(bvh, center, basis_u * math.cos(angle) + basis_v * math.sin(angle))
        for angle in (math.tau * i / sample_count for i in range(sample_count))
    ]

def _ray_radius(bvh: BVHTree, center: Vector, direction: Vector) -> float:
    """Return one ray hit radius."""
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

def _slice_plane_basis(normal: Vector) -> tuple[Vector, Vector]:
    """Build two axes inside the slice plane."""
    reference = Vector((1.0, 0.0, 0.0))
    if abs(normal.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))
    basis_u = reference - normal * normal.dot(reference)
    basis_u.normalize()
    basis_v = normal.cross(basis_u)
    basis_v.normalize()
    return basis_u, basis_v

def _normal_vector(values: Iterable[object]) -> Vector:
    """Decode and normalize a normal vector."""
    vector = Vector(tuple(_as_float(value) for value in values))
    if len(vector) != 3 or vector.length == 0.0:
        raise ValueError("normal must be a non-zero vector with three components")
    vector.normalize()
    return vector

def _as_int(value: object) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)

def _as_float(value: object) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)

if __name__ == "__main__":
    start_sonification_server()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop_sonification_server()
