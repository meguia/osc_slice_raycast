# OSC Slice Raycast

Fast Blender slice sampling using OSC and `BVHTree` ray casts.

## Files

- `blender_osc_slice.py`: OSC server in Blender. Sends only `/slice/radii`.
- `blender_osc_slice_gn.py`: same, but also updates a carrier mesh for Geometry Nodes.
- `send_slice_demo.py`: sends demo `/slice/set` messages and prints `/slice/radii`.
- `slice_demo.tsv`: demo input data.
- `slice_osc.blend`: example Blender file.

## Blender Setup

Install `oscpy` in Blender's Python environment.

Put mesh objects in a collection named:

```text
Objects
```

Run one of these in Blender's Python console:

```python
import blender_osc_slice
blender_osc_slice.start_osc_slice()
```

or:

```python
import blender_osc_slice_gn
blender_osc_slice_gn.start_osc_slice_gn()
```

Stop:

```python
blender_osc_slice.stop_osc_slice()
```

or:

```python
blender_osc_slice_gn.stop_osc_slice_gn()
```

## OSC

Blender receives:

```text
/slice/set object_index radial_sample_count normal_x normal_y normal_z distance
```

Blender sends:

```text
/slice/radii object_index radius_0 ... radius_N_minus_1
```

Clear cached BVH:

```text
/slice/cache/clear
```

## Demo Sender

From a normal Python environment with `oscpy`:

```powershell
python send_slice_demo.py
```

Use a different interval:

```powershell
python send_slice_demo.py --interval 1
```

The sender reads `slice_demo.tsv`.

## Geometry Nodes Version

`blender_osc_slice_gn.py` creates a mesh object named:

```text
SliceGN-<source object name>
```

Add a Geometry Nodes modifier to that object and use the input geometry:

```text
Group Input Geometry -> Mesh to Curve -> Curve to Mesh -> Group Output
```

Control material and visual style inside Geometry Nodes.
