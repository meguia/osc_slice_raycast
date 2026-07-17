# OSC Slice Raycast

This project separates high-resolution slice calculation for sonification from
lower-resolution Blender visualization.

## Architecture

### Sonification slicer

`blender_slice_sonification_server.py` runs in Blender background mode. It uses
the high-resolution meshes, builds every BVH before opening its OSC port, and
processes valid requests through one FIFO worker. It does not create or update
scene visuals.

Start it with:

```bash
blender -b slice_osc_collection.blend --python blender_slice_sonification_server.py
```

It receives on UDP port `9000`:

```text
/slice/get message_id object_id num_samples normal_x normal_y normal_z distance
```

It sends to UDP port `9001`:

```text
/slice/radii message_id object_id radius_0 ... radius_N_minus_1
```

Every valid request received by Python is queued and answered once in FIFO
order. The server prints request rate, raycast rate, queue depth, and maximum
latency every five seconds. A growing queue means the sender must reduce its
request rate.

OSC uses UDP, so `message_id` should also be used by sclang to detect loss before
the request reaches Python. The application does not intentionally discard or
coalesce sonification requests.

### Visualizer

`blender_slice_visualizer.py` runs in graphical Blender with a proxy-only
`.blend`. It receives on UDP port `9005` and sends no OSC messages.

Start Blender with the visual file and script:

```bash
blender slice_osc_visual_proxy.blend --python blender_slice_visualizer.py
```

Assign an object and cutting plane to a fixed slot:

```text
/slice/set slot_id object_id normal_x normal_y normal_z distance
```

Show or hide that slot:

```text
/slice/show slot_id visible
```

Prebuild low-resolution BVHs before using objects:

```text
/preload object_id object_id ...
```

`/slice/set` does not change visibility. A hidden slot keeps its latest assigned
content parked and is ready when `/slice/show slot_id 1` arrives. The same object
can be assigned to multiple slots.

The visualizer uses 128 samples per loop. If several updates for one slot arrive
within one Blender timer tick, only the latest visual state for that slot is
drawn. This coalescing applies only to graphics.

### Single-slot end-to-end demo

With the sonification server and visualizer running, install `oscpy` in the
normal Python environment used for the sender and run:

```bash
python -m pip install oscpy
python send_slice_demo.py
```

The demo exercises both Blender processes with the same cutting plane. It sends
`/slice/get` to the sonification server, sends `/slice/set` and `/slice/show` to
the visualizer, receives `/slice/radii`, and validates the message ID, object ID,
and number of returned radii. The defaults use object `0`, visual slot `0`, and
the 1024 samples used to initialize the rotational wave-terrain buffers in
`concert-paleolithics.scd`.

It makes one full rotation of the normal `[0, 1, 0]` around the X axis and then
leaves the final slice visible for inspection. For a shorter smoke test or a
different object:

```bash
python send_slice_demo.py --frames 4 --object-id 3
```

Run `python send_slice_demo.py --help` for port, timing, sample-count, slot, and
output options. A successful run ends with `TEST PASSED`; a missing or malformed
server reply ends with `TEST FAILED` and a nonzero exit status.

### Multi-slot demos

With the same sonification server and visualizer running, these demos exercise
slot occupation, slot freeing, independent plane modulation, and server replies.
They request 256 radii per active slot from the sonification server at 50 Hz by
default and wait for every matching `/slice/radii` reply for each request tick.
The visualizer is updated independently at 20 Hz by default.

Copy one source object into four visual slots, with a different modulation for
each slot:

```bash
python send_four_slot_same_object_demo.py
```

The default run uses object `0`, slots `0` through `3`, two occupy/free cycles,
and one-second slot transitions. Each cycle occupies one additional slot per
second, then frees one slot per second.

Run seven independent objects in seven slots:

```bash
python send_seven_object_demo.py
```

The default object IDs are `0,2,3,6,9,12,18`, mapped to slots `0` through `6`.
The default run uses one occupy/free cycle with the same one-second transitions.

Useful overrides:

```bash
python send_four_slot_same_object_demo.py --object-id 3 --samples 1024
python send_seven_object_demo.py --objects 0,1,2,3,4,5,6 --cycles 2
python send_seven_object_demo.py --server-rate 80 --visual-rate 20
python send_seven_object_demo.py --visual-rate 15 --slot-interval 0.5
```

Both scripts end by hiding their slots. Run either script with `--help` for the
full port and timing options.

## Object IDs

Integer IDs do not depend on Blender collection order. Every high-resolution
mesh and source proxy must have a unique integer custom property:

```text
slice_index
```

The existing preparation scripts assign this property. The runtime scripts assume
the `.blend` files already contain correct, unique IDs.

## Blender Files

The high-resolution sonification file must contain:

```text
Objects
  meshes with unique slice_index properties
```

Create `slice_osc_visual_proxy.blend` as a lightweight copy containing no
high-resolution `Objects` collection. Keep these source proxies:

```text
Object_Proxy
  SliceObjectProxy_00 ... SliceObjectProxy_19
```

Each source proxy must retain its `slice_index`. The source proxy objects hold
the low-resolution mesh data and remain parked outside the camera view.

Pre-create these 20 independent slot triples in visible collections:

```text
SliceSlotProxy_00 ... SliceSlotProxy_19   mesh objects
SliceSlotPlane_00 ... SliceSlotPlane_19   mesh objects
SliceSlotLoop_00  ... SliceSlotLoop_19    curve objects
```

The one-time helper creates or repairs these slot objects:

```bash
blender slice_osc_visual_proxy.blend --python prepare_slice_visualizer_slots.py
```

Save the `.blend` after it finishes. The helper does not remove the
high-resolution collection; remove that collection from the proxy-only copy
before saving it.

The plane mesh data and loop curve data must be independent for every slot. Slot
proxy mesh data is replaced with shared source-proxy mesh data when assigned.
The runtime reuses all slot objects and does not create or delete datablocks.

The visual file should already have plane/loop materials assigned, an active
camera, and a camera target. The runtime does not assign materials, but it does
animate the camera target and lens while slots become occupied or free.

```text
DynamicSliceCameraTarget
Dynamic Slice Plane Material
Dynamic Slice Loop Material
an active scene camera
```

All slots start parked at `(100, 0, 0)`. Their fixed positions form a centered
5 by 4 row-major grid. Camera setup is owned by the `.blend`; camera animation
is handled by the visualizer runtime.

## Blender Python Dependency

Both Blender runtimes require `oscpy` in Blender's Python environment. Locate
that interpreter with:

```bash
blender -b --python-expr "import sys; print(sys.executable)"
```

Then install `oscpy` using the printed Python executable.

## Stopping

The background sonification process can be stopped with `Ctrl+C`. When running
the scripts interactively in Blender, their public stop functions are:

```python
stop_sonification_server()
stop_slice_visualizer()
```

The earlier `blender_slice_osc*.py` and
`blender_slice_osc_visual_dynamical*.py` files remain as legacy experiments.
`blender_slice_osc_visual_dynamical_parallel.py` is not a worker-based parallel
implementation; it primarily differs by using ports `9005` and `9006`.
