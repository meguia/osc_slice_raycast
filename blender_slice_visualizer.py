"""
Proxy-only OSC slice visualizer with fixed slots.

Incoming:
    /slice/set slot_id object_id normal_x normal_y normal_z distance
    /slice/show slot_id visible
    /preload object_id object_id ...

This service sends no OSC messages.
"""

from __future__ import annotations
import math
import queue
from collections import deque
from dataclasses import dataclass
from typing import Iterable
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from oscpy.server import OSCThreadServer

PROXY_COLLECTION_NAME = "Object_Proxy"  # Source proxy collection.
SOURCE_PROXY_PREFIX = "SliceObjectProxy"
SLOT_PROXY_PREFIX = "SliceSlotProxy"
SLOT_PLANE_PREFIX = "SliceSlotPlane"
SLOT_LOOP_PREFIX = "SliceSlotLoop"

OSC_RECEIVE_HOST = "0.0.0.0"
OSC_RECEIVE_PORT = 9005
UPDATE_INTERVAL_SECONDS = 1.0 / 120.0  # Blender timer interval.

GRID_COLUMNS = 5
GRID_ROWS = 4
SLOT_COUNT = GRID_COLUMNS * GRID_ROWS  # Fixed visual slots.
SLOT_SPACING_X = 3.0
SLOT_SPACING_Y = 3.0
PARK_LOCATION = (100.0, 0.0, 0.0)  # Off-camera parking point.

VISUAL_SAMPLE_COUNT = 128  # Loop resolution.
RAY_EPSILON = 1.0e-7  # Avoid origin self-hits.
PLANE_SIZE = 2.6
LOOP_BEVEL_DEPTH = 0.008
CAMERA_TARGET_NAME = "DynamicSliceCameraTarget"  # Camera target object.
CAMERA_LENS_RANGE = (28.0, 70.0)  # Lens clamp.
CAMERA_FRAME_SCALE = 1.8
CAMERA_SLOT_PADDING = 2.8
CAMERA_MIN_DISTANCE = 5.0
CAMERA_POSITION_TIME = 0.9
CAMERA_ZOOM_TIME = 1.2

@dataclass
class SlotState:
    """Current content and visibility for one slot."""
    object_id: int | None = None
    visible: bool = False

_server: OSCThreadServer | None = None  # OSC listener.
_timer_running = False  # Timer gate.
_incoming: "queue.Queue[tuple[str, tuple[object, ...]]]" = queue.Queue()  # OSC queue.
_source_proxies: dict[int, bpy.types.Object] = {}  # object_id -> proxy.
_proxy_bvhs: dict[int, BVHTree] = {}  # object_id -> BVH.
_slots = {slot_id: SlotState() for slot_id in range(SLOT_COUNT)}  # Slot state.
_preload_queue: "deque[int]" = deque()  # BVHs to build.
_camera_initialized = False  # Snap camera once.

def start_slice_visualizer() -> None:
    """Start the visualizer service."""
    global _server, _timer_running, _source_proxies
    if _server is not None:
        print("Slice visualizer is already running")
        return
    _source_proxies = _source_proxies_by_id()
    server = OSCThreadServer()
    server.listen(address=OSC_RECEIVE_HOST, port=OSC_RECEIVE_PORT, default=True)
    server.bind(b"/slice/set", lambda *args: _incoming.put(("set", args)))
    server.bind(b"/slice/show", lambda *args: _incoming.put(("show", args)))
    server.bind(b"/preload", lambda *args: _incoming.put(("preload", args)))
    _server = server
    _timer_running = True
    bpy.app.timers.register(_process_osc, first_interval=0.0)
    print(
        f"Proxy slice visualizer listening on "
        f"{OSC_RECEIVE_HOST}:{OSC_RECEIVE_PORT}"
    )

def stop_slice_visualizer() -> None:
    """Stop the visualizer service."""
    global _server, _timer_running
    if _server is not None:
        _server.stop_all()
        _server = None
    _timer_running = False
    print("Proxy slice visualizer stopped")

def _process_osc() -> float | None:
    """Apply queued OSC messages on Blender's main thread."""
    if not _timer_running:
        return None
    latest_sets: dict[int, tuple[object, ...]] = {}
    latest_shows: dict[int, tuple[object, ...]] = {}
    preload_messages = []
    while True:
        try:
            command, args = _incoming.get_nowait()
        except queue.Empty:
            break
        try:
            if command == "set":
                latest_sets[_as_int(args[0])] = args
            elif command == "show":
                latest_shows[_as_int(args[0])] = args
            elif command == "preload":
                preload_messages.append(args)
        except Exception as exc:
            print(f"Invalid /slice/{command}: {exc}")
    for args in preload_messages:
        try:
            _request_preload(*args)
        except Exception as exc:
            print(f"Invalid /preload: {exc}")
    for args in latest_sets.values():
        try:
            _set_slot_slice(*args)
        except Exception as exc:
            print(f"Invalid /slice/set: {exc}")
    for args in latest_shows.values():
        try:
            _set_slot_visibility(*args)
        except Exception as exc:
            print(f"Invalid /slice/show: {exc}")
    _preload_one()
    _update_camera()
    return UPDATE_INTERVAL_SECONDS

def _set_slot_slice(*args: object) -> None:
    """Apply one /slice/set message."""
    slot_id = _as_int(args[0])
    object_id = _as_int(args[1])
    source = _source_proxies[object_id]
    normal = _normal_vector(args[2:5])
    distance = _as_float(args[5])
    bvh = _bvh_for_proxy(object_id)
    proxy = _slot_object(SLOT_PROXY_PREFIX, slot_id)
    proxy.data = source.data
    source_matrix = source.matrix_world.copy()
    source_matrix.translation = proxy.location
    proxy.matrix_world = source_matrix
    points, basis_u, basis_v = _sample_slice(
        bvh,
        normal,
        distance,
        VISUAL_SAMPLE_COUNT,
    )
    _update_plane(slot_id, proxy, normal, distance, basis_u, basis_v)
    _update_loop(slot_id, proxy, points)
    _slots[slot_id].object_id = object_id
    _place_slot(slot_id)

def _set_slot_visibility(*args: object) -> None:
    """Apply one /slice/show message."""
    slot_id = _as_int(args[0])
    _slots[slot_id].visible = bool(_as_int(args[1]))
    _place_slot(slot_id)

def _request_preload(*args: object) -> None:
    """Queue proxy BVHs for preload."""
    for value in args:
        object_id = _as_int(value)
        if object_id not in _proxy_bvhs:
            _preload_queue.append(object_id)

def _preload_one() -> None:
    """Build one queued proxy BVH."""
    if _preload_queue:
        object_id = _preload_queue.popleft()
        if object_id not in _proxy_bvhs:
            _bvh_for_proxy(object_id)

def _place_slot(slot_id: int) -> None:
    """Move one slot to grid or parking."""
    state = _slots[slot_id]
    has_visible_content = state.visible and state.object_id is not None
    location = _slot_position(slot_id) if has_visible_content else Vector(PARK_LOCATION)
    for obj in _slot_objects(slot_id):
        obj.location = location

def _slot_position(slot_id: int) -> Vector:
    """Return the grid position for one slot."""
    row = slot_id // GRID_COLUMNS
    column = slot_id % GRID_COLUMNS
    x = (column - ((GRID_COLUMNS - 1) * 0.5)) * SLOT_SPACING_X
    y = (((GRID_ROWS - 1) * 0.5) - row) * SLOT_SPACING_Y
    return Vector((x, y, 0.0))

def _update_camera() -> None:
    """Animate the camera target and lens."""
    global _camera_initialized
    visible_slots = [
        slot_id
        for slot_id, state in _slots.items()
        if state.visible and state.object_id is not None
    ]
    if not visible_slots:
        return
    camera = bpy.context.scene.camera
    target = bpy.data.objects[CAMERA_TARGET_NAME]
    positions = [_slot_position(slot_id) for slot_id in visible_slots]
    min_x = min(position.x for position in positions)
    max_x = max(position.x for position in positions)
    min_y = min(position.y for position in positions)
    max_y = max(position.y for position in positions)
    target_location = Vector((((min_x + max_x) * 0.5), ((min_y + max_y) * 0.5), 0.0))
    width = (max_x - min_x) + CAMERA_SLOT_PADDING
    height = (max_y - min_y) + CAMERA_SLOT_PADDING
    radius = max(
        math.sqrt((width * 0.5) ** 2 + (height * 0.5) ** 2) * CAMERA_FRAME_SCALE,
        0.001,
    )
    position_alpha = 1.0 - math.exp(-UPDATE_INTERVAL_SECONDS / CAMERA_POSITION_TIME)
    zoom_alpha = 1.0 - math.exp(-UPDATE_INTERVAL_SECONDS / CAMERA_ZOOM_TIME)
    if _camera_initialized:
        target.location = target.location.lerp(target_location, position_alpha)
    else:
        target.location = target_location
        _camera_initialized = True
    render = bpy.context.scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / (
        render.resolution_y * render.pixel_aspect_y
    )
    sensor = min(camera.data.sensor_width, camera.data.sensor_width / aspect)
    distance = max((camera.location - target.location).length, CAMERA_MIN_DISTANCE)
    lens = sensor * distance / (2.0 * radius)
    lens = min(max(lens, CAMERA_LENS_RANGE[0]), CAMERA_LENS_RANGE[1])
    camera.data.lens += (lens - camera.data.lens) * zoom_alpha
    target.update_tag()
    camera.update_tag()

def _update_plane(slot_id: int, proxy: bpy.types.Object, normal: Vector, distance: float, basis_u: Vector, basis_v: Vector) -> None:
    """Rebuild one slot plane mesh."""
    plane = _slot_object(SLOT_PLANE_PREFIX, slot_id)
    center = normal * distance
    half = PLANE_SIZE * 0.5
    vertices = [center + ((-basis_u - basis_v) * half), center + ((basis_u - basis_v) * half), center + ((basis_u + basis_v) * half), center + ((-basis_u + basis_v) * half)]
    plane.data.clear_geometry()
    plane.data.from_pydata([(v.x, v.y, v.z) for v in vertices], [], [(0, 1, 2, 3)])
    plane.data.update()
    plane.matrix_world = proxy.matrix_world.copy()

def _update_loop(slot_id: int, proxy: bpy.types.Object, points: list[tuple[float, float, float] | None]) -> None:
    """Rebuild one slot loop curve."""
    loop = _slot_object(SLOT_LOOP_PREFIX, slot_id)
    curve = loop.data
    while curve.splines:
        curve.splines.remove(curve.splines[0])
    valid_points = [point for point in points if point is not None]
    if len(valid_points) >= 2:
        spline = curve.splines.new("POLY")
        spline.points.add(len(valid_points) - 1)
        for point, coordinates in zip(spline.points, valid_points):
            point.co = (*coordinates, 1.0)
        spline.use_cyclic_u = len(valid_points) == len(points)
    curve.bevel_depth = LOOP_BEVEL_DEPTH
    curve.resolution_u = 1
    curve.bevel_resolution = 3
    loop.matrix_world = proxy.matrix_world.copy()

def _sample_slice(bvh: BVHTree, normal: Vector, distance: float, sample_count: int) -> tuple[list[tuple[float, float, float] | None], Vector, Vector]:
    """Sample proxy hit points around one slice."""
    basis_u, basis_v = _slice_plane_basis(normal)
    center = normal * distance
    points = []
    for sample_index in range(sample_count):
        angle = math.tau * sample_index / sample_count
        direction = basis_u * math.cos(angle) + basis_v * math.sin(angle)
        radius = _ray_radius(bvh, center, direction)
        point = center + direction * radius
        points.append((point.x, point.y, point.z) if radius > RAY_EPSILON else None)
    return points, basis_u, basis_v

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

def _bvh_for_proxy(object_id: int) -> BVHTree:
    """Return or build one proxy BVH."""
    cached = _proxy_bvhs.get(object_id)
    if cached is not None:
        return cached
    proxy = _source_proxies[object_id]
    mesh = proxy.data
    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    _proxy_bvhs[object_id] = bvh
    return bvh

def _source_proxies_by_id() -> dict[int, bpy.types.Object]:
    """Map slice_index values to source proxies."""
    prefix = SOURCE_PROXY_PREFIX + "_"
    return {
        int(obj["slice_index"]): obj
        for obj in bpy.data.collections[PROXY_COLLECTION_NAME].all_objects
        if obj.name.startswith(prefix)
    }

def _slot_objects(slot_id: int) -> tuple[bpy.types.Object, bpy.types.Object, bpy.types.Object]:
    """Return proxy, plane, and loop for one slot."""
    return (
        _slot_object(SLOT_PROXY_PREFIX, slot_id),
        _slot_object(SLOT_PLANE_PREFIX, slot_id),
        _slot_object(SLOT_LOOP_PREFIX, slot_id),
    )

def _slot_object(prefix: str, slot_id: int) -> bpy.types.Object:
    """Return one named slot object."""
    return bpy.data.objects[f"{prefix}_{slot_id:02d}"]

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
    start_slice_visualizer()