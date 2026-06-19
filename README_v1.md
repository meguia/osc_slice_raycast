# OSC Slice Raycast v1

Minimal server:

```text
blender_osc_slice_v1.py
slice_osc_v1.blend
```

This version only receives:

```text
/slice/set object_index radial_sample_count normal_x normal_y normal_z distance
```

and only sends:

```text
/slice/radii object_index radius_0 ... radius_N_minus_1
```

## Install `oscpy` In Blender

First find Blender's Python executable:

```bash
blender -b --python-expr "import sys; print(sys.executable)"
```

Use the printed path to install `oscpy`.

```bash
"/path/to/blender/python/bin/python3.11" -m ensurepip
"/path/to/blender/python/bin/python3.11" -m pip install oscpy
```

Adjust the Blender version number if your folder is different.

## Run The Server

From this repository folder:

```bash
blender -b slice_osc_v1.blend --python blender_osc_slice_v1.py
```

Default ports:

```text
receive: 0.0.0.0:9000
send:    127.0.0.1:9001
```
