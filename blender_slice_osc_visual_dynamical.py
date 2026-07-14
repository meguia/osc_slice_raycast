"""
Dynamic OSC slice visual server for Blender.

Incoming:
    /show object_index visible
    /slice/set message_id object_index samples normal_x normal_y normal_z distance

Outgoing:
    /slice/radii message_id object_index and the list of sample radii
    /osc/error message

Hi-res objects in the "Objects" collection are kept hidden and used only for
BVH raycasts. Matching low-res proxy objects in "Object_Proxy" stay visible but
parked at PARK_LOCATION while inactive. /show index 1 moves a proxy into the
lowest free slot in a centered 5x4 grid; /show index 0 parks it again.
"""

from __future__ import annotations

import math
import time
import queue

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

# Config
SOURCE_COLLECTION_NAME = "Objects"

PROXY_OBJECT_PREFIX = "SliceObjectProxy"
PLANE_OBJECT_PREFIX = "SlicePlaneDynamic"
LOOP_OBJECT_PREFIX = "SliceLoopDynamic"

OSC_RECEIVE_HOST = "0.0.0.0"
OSC_RECEIVE_PORT = 9000
OSC_SEND_HOST = "127.0.0.1"
OSC_SEND_PORT = 9001

SLICE_SEND_ADDRESS = b"/slice/radii"
ERROR_SEND_ADDRESS = b"/osc/error"

UPDATE_INTERVAL_SECONDS = 1.0 / 250.0
RAY_EPSILON = 1.0e-7

GRID_COLUMNS = 5
GRID_ROWS = 4
SLOT_SPACING_X = 3.0
SLOT_SPACING_Y = 3.0
PARK_LOCATION = (100.0, 0.0, 0.0)

PLANE_SIZE = 2.6
LOOP_BEVEL_DEPTH = 0.008
SHOW_SLICE_PLANE = True
SHOW_SLICE_LOOP = True
SLOW_STEP_LOG_SECONDS = 0.1

AUTO_CAMERA_ENABLED = True
AUTO_CAMERA_TARGET_NAME = "DynamicSliceCameraTarget"
AUTO_CAMERA_MIN_FOCAL_LENGTH_MM = 28.0
AUTO_CAMERA_MAX_FOCAL_LENGTH_MM = 70.0
AUTO_CAMERA_FRAME_SCALE = 1.8
AUTO_CAMERA_SLOT_PADDING = 2.8
AUTO_CAMERA_MIN_DISTANCE = 5.0
AUTO_CAMERA_POSITION_TIME_SECONDS = 0.9
AUTO_CAMERA_ZOOM_TIME_SECONDS = 1.2

# State
_server: OSCThreadServer | None = None
_client: OSCClient | None = None
_timer_running = False
_incoming: "queue.Queue[tuple[str, tuple[object, ...]]]" = queue.Queue()

_mesh_caches: dict[str, dict[str, object]] = {}
_source_object_cache: list[bpy.types.Object] | None = None
_active_slots: dict[int, int] = {}
_slot_owners: dict[int, int] = {}
_camera_initialized = False

# OSC callbacks
def _enqueue_show(*args: object) -> None:
    _incoming.put(("show", args))

def _enqueue_set_slice(*args: object) -> None:
    _incoming.put(("slice", args))

def _enqueue_preload(*args: object) -> None:
    _incoming.put(("preload", args))

# Lifecycle
def start_osc_slice_dynamic() -> None:
    """Start OSC input/output and Blender timer processing."""
    global _client, _server, _timer_running

    if _client is None:
        _client = OSCClient(OSC_SEND_HOST, OSC_SEND_PORT)

    if _server is None:
        server = OSCThreadServer()
        server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
        server.bind(b"/show", _enqueue_show)
        server.bind(b"/slice/set", _enqueue_set_slice)
        server.bind(b"/preload", _enqueue_preload)
        _server = server

    if not _timer_running:
        bpy.app.timers.register(_process_osc, first_interval=0.0)
        _timer_running = True

    print(
        "OSC dynamic slice visual listening on "
        f"{OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}; sending to "
        f"{OSC_SEND_HOST}:{OSC_SEND_PORT}"
    )

def stop_osc_slice_dynamic() -> None:
    """Stop OSC input/output and clear slot bookkeeping."""
    global _client, _server, _timer_running, _camera_initialized, _source_object_cache

    if _server is not None:
        _server.stop_all()
        _server = None

    _client = None
    _timer_running = False
    _active_slots.clear()
    _slot_owners.clear()
    _camera_initialized = False

    print("OSC dynamic slice visual stopped")

# Message handling
def _process_osc() -> float | None:
    if not _timer_running:
        return None

    commands = []
    while True:
        try:
            commands.append(_incoming.get_nowait())
        except queue.Empty:
            break

    for command, args in commands:
        try:
            if command == "show":
                _set_show(*args)
            elif command == "preload":
                _set_preload(*args)
            elif command == "slice":
                _set_slice(*args)
        except Exception as exc:
            _send_error(f"{command}: {exc}")
            print(f"OSC dynamic slice visual error ({command}): {exc}")

    if AUTO_CAMERA_ENABLED:
        _update_auto_camera()

    return UPDATE_INTERVAL_SECONDS

def _set_show(*args: object) -> None:
    if len(args) != 2:
        raise ValueError("/show expects object_index visible")

    object_index = _as_int(args[0])
    visible = _as_bool(args[1])
    started = time.perf_counter()

    if visible:
        _show_object(object_index)
    else:
        _hide_object(object_index)

    _log_slow(f"/show object={object_index} visible={int(visible)}", started)

def _set_slice(*args: object) -> None:
    if len(args) != 7:
        raise ValueError("/slice/set expects message_id object_index samples nx ny nz distance")

    command_started = time.perf_counter()
    message_id = _as_int(args[0])
    object_index = _as_int(args[1])
    sample_count = _as_int(args[2])
    if sample_count <= 0:
        raise ValueError("sample count must be greater than zero")

    if object_index not in _active_slots:
        print(f"Ignoring /slice/set id={message_id} for inactive object index {object_index}")
        return

    source = _source_object_by_index(object_index)
    proxy = _proxy_object_by_index(object_index)
    normal = _normal_vector(args[3:6])
    distance = _as_float(args[6])

    step_started = time.perf_counter()
    bvh = _bvh_for_object(source)
    _log_slow(f"BVH object={object_index}", step_started)

    step_started = time.perf_counter()
    radii, points, basis_u, basis_v = _sample_slice(bvh, normal, distance, sample_count)
    _log_slow(f"sample object={object_index}", step_started)

    step_started = time.perf_counter()
    if SHOW_SLICE_PLANE:
        _update_plane_object(object_index, proxy, normal, distance, basis_u, basis_v)
    if SHOW_SLICE_LOOP:
        _update_loop_object(object_index, proxy, points)
    _log_slow(f"visual object={object_index}", step_started)

    _send_message(SLICE_SEND_ADDRESS, [message_id, object_index, *radii])
    _log_slow(f"/slice/set id={message_id} object={object_index}", command_started)

def _set_preload(*args: object) -> None:
    object_indices = [_as_int(arg) for arg in args] if args else list(range(len(_source_objects())))
    started = time.perf_counter()
    print(f"Preloading dynamic slice runtime for objects {object_indices}")

    for object_index in object_indices:
        source = _source_object_by_index(object_index)
        step_started = time.perf_counter()
        _bvh_for_object(source)
        _log_slow(f"preload BVH object={object_index}", step_started)

        _park_object(_proxy_object_by_index(object_index))

        if SHOW_SLICE_PLANE:
            _park_object(bpy.data.objects[f"{PLANE_OBJECT_PREFIX}_{object_index:02d}"])
        if SHOW_SLICE_LOOP:
            _park_object(bpy.data.objects[f"{LOOP_OBJECT_PREFIX}_{object_index:02d}"])

    _log_slow("/preload", started)
    print("Preload complete")

# Slots and camera
def _show_object(object_index: int) -> None:
    proxy = _proxy_object_by_index(object_index)

    if object_index not in _active_slots:
        slot_index = next(
            slot
            for slot in range(GRID_COLUMNS * GRID_ROWS)
            if slot not in _slot_owners
        )
        _active_slots[object_index] = slot_index
        _slot_owners[slot_index] = object_index
        print(f"Showing object {object_index} in slot {slot_index}")
    else:
        slot_index = _active_slots[object_index]

    proxy.location = _slot_position(slot_index)
    proxy.update_tag()
    _ensure_object_visible(proxy)

def _hide_object(object_index: int) -> None:
    slot_index = _active_slots.pop(object_index, None)
    if slot_index is not None:
        _slot_owners.pop(slot_index, None)
        print(f"Parking object {object_index} from slot {slot_index}")

    _park_object(_proxy_object_by_index(object_index))

    for obj in (
        bpy.data.objects.get(f"{PLANE_OBJECT_PREFIX}_{object_index:02d}"),
        bpy.data.objects.get(f"{LOOP_OBJECT_PREFIX}_{object_index:02d}"),
    ):
        if obj is not None:
            _park_object(obj)

def _slot_position(slot_index: int) -> Vector:
    row = slot_index // GRID_COLUMNS
    column = slot_index % GRID_COLUMNS
    x = (column - ((GRID_COLUMNS - 1) * 0.5)) * SLOT_SPACING_X
    y = (((GRID_ROWS - 1) * 0.5) - row) * SLOT_SPACING_Y
    return Vector((x, y, 0.0))

def _update_auto_camera() -> None:
    if not _active_slots:
        return

    global _camera_initialized

    camera = bpy.context.scene.camera
    target = bpy.data.objects[AUTO_CAMERA_TARGET_NAME]
    positions = [_slot_position(slot_index) for slot_index in _active_slots.values()]

    min_x = min(position.x for position in positions)
    max_x = max(position.x for position in positions)
    min_y = min(position.y for position in positions)
    max_y = max(position.y for position in positions)

    target_location = Vector((((min_x + max_x) * 0.5), ((min_y + max_y) * 0.5), 0.0))
    width = (max_x - min_x) + AUTO_CAMERA_SLOT_PADDING
    height = (max_y - min_y) + AUTO_CAMERA_SLOT_PADDING
    frame_radius = max(
        math.sqrt((width * 0.5) ** 2 + (height * 0.5) ** 2)
        * AUTO_CAMERA_FRAME_SCALE,
        0.001,
    )
    position_alpha = 1.0 - math.exp(
        -UPDATE_INTERVAL_SECONDS / AUTO_CAMERA_POSITION_TIME_SECONDS
    )
    zoom_alpha = 1.0 - math.exp(
        -UPDATE_INTERVAL_SECONDS / AUTO_CAMERA_ZOOM_TIME_SECONDS
    )

    if not _camera_initialized:
        target.location = target_location
        _camera_initialized = True
    else:
        target.location = target.location.lerp(target_location, position_alpha)

    render = bpy.context.scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / (
        render.resolution_y * render.pixel_aspect_y
    )
    sensor_size = min(camera.data.sensor_width, camera.data.sensor_width / aspect)
    distance = max((camera.location - target.location).length, AUTO_CAMERA_MIN_DISTANCE)
    desired_lens = sensor_size * distance / (2.0 * frame_radius)
    desired_lens = min(
        max(desired_lens, AUTO_CAMERA_MIN_FOCAL_LENGTH_MM),
        AUTO_CAMERA_MAX_FOCAL_LENGTH_MM,
    )
    camera.data.lens += (desired_lens - camera.data.lens) * zoom_alpha

    target.update_tag()
    camera.update_tag()

# Visuals

def _update_plane_object(
    object_index: int,
    proxy: bpy.types.Object,
    normal: Vector,
    distance: float,
    basis_u: Vector,
    basis_v: Vector,
) -> None:
    plane = bpy.data.objects[f"{PLANE_OBJECT_PREFIX}_{object_index:02d}"]
    center = normal * distance
    half = PLANE_SIZE * 0.5
    vertices = [
        center + ((-basis_u - basis_v) * half),
        center + ((basis_u - basis_v) * half),
        center + ((basis_u + basis_v) * half),
        center + ((-basis_u + basis_v) * half),
    ]

    plane.data.clear_geometry()
    plane.data.from_pydata([(v.x, v.y, v.z) for v in vertices], [], [(0, 1, 2, 3)])
    plane.data.update()
    plane.matrix_world = proxy.matrix_world.copy()
    _ensure_object_visible(plane)

    plane.data.materials[0] = bpy.data.materials["Dynamic Slice Plane Material"]

def _update_loop_object(
    object_index: int,
    proxy: bpy.types.Object,
    points: list[tuple[float, float, float] | None],
) -> None:
    loop = bpy.data.objects[f"{LOOP_OBJECT_PREFIX}_{object_index:02d}"]
    curve = loop.data
    while curve.splines:
        curve.splines.remove(curve.splines[0])

    valid_points = [point for point in points if point is not None]
    if len(valid_points) < 2:
        _park_object(loop)
        return

    spline = curve.splines.new("POLY")
    spline.points.add(len(valid_points) - 1)
    for point, co in zip(spline.points, valid_points):
        point.co = (co[0], co[1], co[2], 1.0)
    spline.use_cyclic_u = len(valid_points) == len(points)

    curve.bevel_depth = LOOP_BEVEL_DEPTH
    curve.resolution_u = 1
    curve.bevel_resolution = 3
    loop.matrix_world = proxy.matrix_world.copy()
    _ensure_object_visible(loop)

    curve.materials[0] = bpy.data.materials["Dynamic Slice Loop Material"]

# Slicing

def _bvh_for_object(obj: bpy.types.Object) -> BVHTree:
    mesh = obj.data
    signature = (
        obj.name_full,
        mesh.name_full,
        len(mesh.vertices),
        len(mesh.edges),
        len(mesh.polygons),
    )
    cached = _mesh_caches.get(obj.name_full)
    if cached is not None and cached["signature"] == signature:
        return cached["bvh"]

    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    if not vertices or not triangles:
        raise ValueError(f"{obj.name!r} has no triangles")

    bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    _mesh_caches[obj.name_full] = {"signature": signature, "bvh": bvh}
    return bvh

def _sample_slice(
    bvh: BVHTree,
    normal: Vector,
    distance: float,
    sample_count: int,
) -> tuple[list[float], list[tuple[float, float, float] | None], Vector, Vector]:
    basis_u, basis_v = _slice_plane_basis(normal)
    center = normal * distance
    radii = []
    points = []

    for sample_index in range(sample_count):
        angle = math.tau * sample_index / sample_count
        direction = basis_u * math.cos(angle) + basis_v * math.sin(angle)
        radius = _ray_radius(bvh, center, direction)
        point = center + direction * radius
        radii.append(radius)
        points.append((point.x, point.y, point.z) if radius > RAY_EPSILON else None)

    return radii, points, basis_u, basis_v

def _ray_radius(bvh: BVHTree, center: Vector, direction: Vector) -> float:
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
    reference = Vector((1.0, 0.0, 0.0))
    if abs(normal.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))

    basis_u = reference - normal * normal.dot(reference)
    basis_u.normalize()
    basis_v = normal.cross(basis_u)
    basis_v.normalize()
    return basis_u, basis_v

# Blender helpers

def _park_object(obj: bpy.types.Object) -> None:
    obj.location = PARK_LOCATION
    _ensure_object_visible(obj)

def _ensure_object_visible(obj: bpy.types.Object) -> None:
    obj.hide_viewport = False
    obj.hide_render = False
    try:
        obj.hide_set(False)
    except RuntimeError:
        pass
    obj.update_tag()

def _source_object_by_index(index: int) -> bpy.types.Object:
    return _source_objects()[index]

def _source_objects() -> list[bpy.types.Object]:
    cache = globals().get("_source_object_cache")
    if cache is None:
        collection = bpy.data.collections[SOURCE_COLLECTION_NAME]
        cache = sorted(
            (obj for obj in collection.all_objects if obj.type == "MESH"),
            key=lambda obj: obj.name.lower(),
        )
        globals()["_source_object_cache"] = cache
    return cache

def _proxy_object_by_index(index: int) -> bpy.types.Object:
    return bpy.data.objects[f"{PROXY_OBJECT_PREFIX}_{index:02d}"]

# Conversion and output

def _normal_vector(values: object) -> Vector:
    vector = Vector(tuple(_as_float(value) for value in values))
    if len(vector) != 3 or vector.length == 0.0:
        raise ValueError("normal must be a non-zero vector with three components")
    vector.normalize()
    return vector

def _as_bool(value: object) -> bool:
    return bool(_as_int(value))

def _as_int(value: object) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)

def _as_float(value: object) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)

def _log_slow(label: str, started: float) -> None:
    elapsed = time.perf_counter() - started
    if elapsed >= SLOW_STEP_LOG_SECONDS:
        print(f"Slow {label}: {elapsed:.3f}s")

def _send_error(message: str) -> None:
    print(f"OSC error: {message}")
    _send_message(ERROR_SEND_ADDRESS, [message.encode("utf-8")])

def _send_message(address: bytes, values: list[object]) -> None:
    if _client is None:
        return

    try:
        _client.send_message(address, values)
    except Exception as exc:
        print(f"OSC send failed for {address!r}: {exc}")

if __name__ == "__main__":
    start_osc_slice_dynamic()
