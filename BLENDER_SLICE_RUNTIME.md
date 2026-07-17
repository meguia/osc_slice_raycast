# Blender Slice Runtime Notes

This document dissects the two Blender runtime scripts:

- `blender_slice_sonification_server.py`
- `blender_slice_visualizer.py`

The current design assumes the `.blend` files are the ground truth. The scripts
do not validate or repair scene setup. Materials, camera objects, initial object
parking, object visibility, source collections, slot objects, and custom
`slice_index` values are expected to be correct in the Blender files. Camera
animation is still runtime behavior.

## General Plan

Run two Blender processes:

1. `blender_slice_sonification_server.py` in background mode with the
   high-resolution collection file.
2. `blender_slice_visualizer.py` in graphical Blender with the prepared visual
   proxy file.

The sender sends the same slice plane information to both processes:

```text
normal_x normal_y normal_z distance
```

The sonification server calculates high-resolution radii and replies over OSC.
The visualizer updates fixed low-resolution display slots and sends no replies.

## OSC Contract

Sonification input on UDP port `9000`:

```text
/slice/get message_id object_id num_samples normal_x normal_y normal_z distance
```

Sonification output on UDP port `9001`:

```text
/slice/radii message_id object_id radius_0 ... radius_N_minus_1
```

Visualizer input on UDP port `9005`:

```text
/slice/set slot_id object_id normal_x normal_y normal_z distance
/slice/show slot_id visible
/preload object_id object_id ...
```

## Trusted Blend Assumption

The scripts are now intentionally thin. They rely on the Blender files for
setup correctness instead of checking it again at runtime.

The sonification `.blend` is expected to contain:

- an `Objects` collection;
- mesh objects inside that collection;
- a unique integer `slice_index` custom property on each mesh;
- valid mesh triangle data.

The visualization `.blend` is expected to contain:

- an `Object_Proxy` collection;
- source proxy objects named `SliceObjectProxy_*`;
- a unique integer `slice_index` custom property on each source proxy;
- 20 slot proxy, plane, and loop objects named with `_00` through `_19`;
- materials already assigned to slot planes and loops;
- independent mesh/curve data for every slot plane and loop;
- visible objects already configured correctly;
- source proxies and empty slots already parked;
- an active camera and `DynamicSliceCameraTarget` object.

If the `.blend` is wrong, the scripts will usually fail naturally at the Blender
lookup or data-use point. That is intentional: the file is the source of truth.

## Geometry Model

Both scripts use the same slice algorithm:

1. Normalize the incoming plane normal.
2. Build two orthonormal vectors inside the slice plane.
3. Compute the plane center as `normal * distance`.
4. Cast radial rays from that center.
5. Use ray-hit distances as slice-loop radii.

The server sends radii. The visualizer turns successful ray hits into a curve
loop and draws the slice plane.

## Minimality Policy

The Blender scripts remain self-contained even though they duplicate a few
helpers. A shared helper module would reduce duplicate function names, but it
would add another file that must be installed into Blender's Python path. For
this project, two standalone scripts are simpler to run.

Runtime validation and setup repair were removed where the `.blend` already
defines the answer:

- no visual scene validation function;
- no runtime material assignment;
- no runtime object unhide/visibility repair;
- no startup parking reset;
- no camera setup validation;
- no visual slot range checks;
- no source proxy duplicate/missing checks;
- no sonification startup mesh validation beyond using Blender data directly;
- no sonification request object-ID or sample-count checks;
- no sonification request-rate stats logger.

The remaining checks are operational, not scene setup checks:

- OSC callbacks catch exceptions so one bad packet does not kill the service.
- Normals still reject wrong-sized or zero vectors because basis construction
  needs a usable direction.
- Ray misses return `0.0` because open slices are valid runtime results.

## `blender_slice_sonification_server.py`

### Role

This is the high-resolution request/reply service. Run it in background Blender:

```bash
blender -b slice_osc_collection.blend --python blender_slice_sonification_server.py
```

It builds one `BVHTree` per high-resolution object at startup. It then listens
for `/slice/get`, computes radii, and replies with `/slice/radii`.

### State

- `_server`: OSC listener.
- `_worker`: one FIFO worker thread.
- `_requests`: queue of `SliceRequest` objects plus `None` as the stop signal.
- `_bvhs`: cached `object_id -> BVHTree` mapping.

The single worker is deliberate. It keeps reply ordering simple and avoids
parallel access to the same queue and BVH map.

### Class and Function Reference

#### `SliceRequest`

Frozen dataclass for one parsed `/slice/get` request. It stores:

- `message_id`;
- `object_id`;
- `sample_count`;
- normalized-later raw normal tuple;
- slice `distance`.

#### `start_sonification_server()`

Starts the service. It:

1. Returns early if the server is already running.
2. Builds `_bvhs` from the trusted `Objects` collection.
3. Starts the worker thread with an OSC reply client.
4. Starts the OSC listener on port `9000`.
5. Binds `/slice/get` to `_enqueue_slice_get`.

BVHs are built before opening the OSC port so the first request does not pay
mesh setup cost.

#### `stop_sonification_server()`

Stops the OSC listener, sends `None` to the worker queue, joins the worker
briefly, and clears the worker handle.

#### `_enqueue_slice_get(*args)`

OSC callback for `/slice/get`. It converts OSC values into a `SliceRequest` and
puts it on `_requests`.

Malformed packets are caught and printed. No reply is sent for malformed
packets.

#### `_request_worker(client)`

Worker loop. It waits for requests, stops on `None`, samples radii for each
request, and sends:

```text
/slice/radii message_id object_id radii...
```

Slice failures are caught and printed so the worker can continue processing
later requests.

#### `_objects_by_id()`

Creates the source object map directly from the trusted Blender collection:

```python
{int(obj["slice_index"]): obj for mesh objects in Objects}
```

There is no duplicate or missing-ID validation. The `.blend` owns that
correctness.

#### `_build_bvh(obj)`

Calculates loop triangles and builds a `BVHTree` from the object's mesh vertices
and triangle indices.

#### `_sample_radii(bvh, normal, distance, sample_count)`

Creates the slice plane basis, computes the center point, walks evenly spaced
angles around a circle, raycasts each direction, and returns a list of radii.

#### `_ray_radius(bvh, center, direction)`

Raycasts from `center` in `direction`. A miss returns `0.0`. If the first hit is
closer than `RAY_EPSILON`, it tries again from a tiny offset to avoid a false
self-hit at the ray origin.

#### `_slice_plane_basis(normal)`

Builds two unit vectors inside the slice plane. It uses X as the reference
vector unless the normal is too close to X, then uses Y.

#### `_normal_vector(values)`

Converts three OSC values to floats, creates a `Vector`, rejects zero-length or
wrong-sized normals, and normalizes the vector.

#### `_as_int(value)`

Converts an OSC value to `int`, decoding bytes when needed.

#### `_as_float(value)`

Converts an OSC value to `float`, decoding bytes when needed.

#### `if __name__ == "__main__"`

Starts the server and blocks with `threading.Event().wait()`. `Ctrl+C` calls
`stop_sonification_server()`.

## `blender_slice_visualizer.py`

### Role

This is the low-resolution display service. Run it in graphical Blender:

```bash
blender slice_osc_visualization.blend --python blender_slice_visualizer.py
```

The visualizer assumes the visual `.blend` already contains all objects,
materials, visibility state, and camera setup. It moves slot objects, rewrites
per-slot plane/loop geometry, and animates the camera target/lens.

### State

- `_server`: OSC listener.
- `_timer_running`: controls whether the Blender timer reschedules itself.
- `_incoming`: thread-safe queue from OSC callbacks to Blender's main thread.
- `_source_proxies`: cached `object_id -> source proxy object` map.
- `_proxy_bvhs`: runtime low-resolution BVH cache.
- `_slots`: current object assignment and visible flag for each slot.
- `_preload_queue`: object IDs whose proxy BVHs should be built gradually.
- `_camera_initialized`: whether the camera target has been snapped once.

Scene edits happen from `_process_osc`, not directly from OSC callbacks, because
Blender data should be changed on Blender's main thread.

### Class and Function Reference

#### `SlotState`

Dataclass with two fields:

- `object_id`: object assigned to the slot, or `None`;
- `visible`: whether `/slice/show` has marked the slot visible.

A slot is placed in the grid only when it is both visible and assigned.

#### `start_slice_visualizer()`

Starts the visualizer. It:

1. Returns early if already running.
2. Builds `_source_proxies` from the trusted `Object_Proxy` collection.
3. Starts the OSC listener on port `9005`.
4. Binds `/slice/set`, `/slice/show`, and `/preload`.
5. Registers `_process_osc` as a Blender timer.

It does not validate scene setup, assign materials, unhide objects, or park
slots.

#### `stop_slice_visualizer()`

Stops the OSC listener and sets `_timer_running` false so `_process_osc` stops
rescheduling itself.

#### `_process_osc()`

Blender timer callback. It drains `_incoming`, keeping only the latest
`/slice/set` and `/slice/show` per slot for the current tick. This coalescing is
intentional: the visualizer should show the newest state, not every intermediate
packet.

Then it:

1. handles preload messages;
2. applies latest slice updates;
3. applies latest show/hide updates;
4. builds at most one queued proxy BVH;
5. updates the camera target and lens;
6. returns `UPDATE_INTERVAL_SECONDS`.

If `_timer_running` is false, it returns `None` and Blender stops the timer.

#### `_set_slot_slice(*args)`

Applies one `/slice/set` message. It parses slot ID, object ID, normal, and
distance; gets the source proxy and BVH; assigns source mesh data to the slot
proxy; samples the visual slice; updates the slot plane and loop; stores the
slot object ID; and places or parks the slot.

`/slice/set` does not make a slot visible. Visibility comes from `/slice/show`.

#### `_set_slot_visibility(*args)`

Applies one `/slice/show` message. It updates the slot's visible flag and calls
`_place_slot`.

#### `_request_preload(*args)`

Adds object IDs from `/preload` to `_preload_queue` when their BVH is not already
cached.

#### `_preload_one()`

Builds one queued proxy BVH per timer tick. This spreads the cost over time.

#### `_place_slot(slot_id)`

Moves a slot's proxy, plane, and loop to either its grid position or
`PARK_LOCATION`. It does not hide/unhide objects; the `.blend` owns visibility.

#### `_slot_position(slot_id)`

Maps a slot ID to a centered 5 by 4 row-major grid.

#### `_update_camera()`

Animates `DynamicSliceCameraTarget` and the active camera lens to frame occupied
visible slots. It assumes the target object and active camera already exist in
the `.blend`.

#### `_update_plane(slot_id, proxy, normal, distance, basis_u, basis_v)`

Rebuilds the slot plane mesh as one quad in the slice plane and copies the slot
proxy transform to the plane object.

#### `_update_loop(slot_id, proxy, points)`

Rebuilds the slot curve from sampled hit points. It creates a cyclic polyline
only when all samples hit.

#### `_sample_slice(bvh, normal, distance, sample_count)`

Visual equivalent of the server sampling path. It returns hit points plus the
two basis vectors needed to draw the plane.

#### `_ray_radius(bvh, center, direction)`

Same ray behavior as the server: miss returns `0.0`, near-zero hit retries from
a small offset.

#### `_slice_plane_basis(normal)`

Builds two unit basis vectors inside the slice plane.

#### `_bvh_for_proxy(object_id)`

Returns a cached low-resolution proxy BVH, building it from the source proxy
mesh on demand.

#### `_source_proxies_by_id()`

Builds `object_id -> proxy` from objects in `Object_Proxy` whose names start
with `SliceObjectProxy_`.

No duplicate or missing-ID validation is done; the `.blend` is trusted.

#### `_slot_objects(slot_id)`

Returns the three objects for one slot: proxy, plane, and loop.

#### `_slot_object(prefix, slot_id)`

Looks up one slot object by name:

```text
{prefix}_{slot_id:02d}
```

#### `_normal_vector(values)`

Converts three OSC values to floats, creates a `Vector`, rejects zero-length or
wrong-sized normals, and normalizes the vector.

#### `_as_int(value)`

Converts an OSC value to `int`, decoding bytes when needed.

#### `_as_float(value)`

Converts an OSC value to `float`, decoding bytes when needed.

#### `if __name__ == "__main__"`

Starts the visualizer. The registered Blender timer keeps it alive after the
script body finishes.

## Behavior Summary

The sonification server is FIFO and request/reply. Every valid request that
reaches the worker should receive exactly one `/slice/radii` reply.

The visualizer is state based. It coalesces visual packets per timer tick and
draws only the latest state for each slot.

The `.blend` files now carry all setup responsibility. If a material, camera,
slot object, collection, visibility flag, initial parking position, or
`slice_index` is wrong, fix the `.blend`, not the runtime script.
