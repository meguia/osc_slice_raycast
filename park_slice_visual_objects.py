"""
Prepare dynamic slice visual objects for park/move rendering.

Run this in Blender before the OSC server. It creates all dynamic plane/loop
objects, parks proxy/plane/loop visuals at PARK_LOCATION, and makes their
collections visible. The hi-res Objects collection is kept hidden.
"""

from __future__ import annotations

import bpy


SOURCE_COLLECTION_NAME = "Objects"
PROXY_COLLECTION_NAME = "Object_Proxy"
PLANE_COLLECTION_NAME = "Slice Planes"
LOOP_COLLECTION_NAME = "Slice Loops"

PROXY_OBJECT_PREFIX = "SliceObjectProxy"
PLANE_OBJECT_PREFIX = "SlicePlaneDynamic"
LOOP_OBJECT_PREFIX = "SliceLoopDynamic"

PARK_LOCATION = (100.0, 0.0, 0.0)
PLANE_SIZE = 2.6
PLANE_ALPHA = 0.28
LOOP_BEVEL_DEPTH = 0.008
PLANE_MATERIAL_NAME = "Dynamic Slice Plane Material"
LOOP_MATERIAL_NAME = "Dynamic Slice Loop Material"


def main() -> None:
    _set_collection_visible(PROXY_COLLECTION_NAME, True)
    _set_collection_visible(PLANE_COLLECTION_NAME, True)
    _set_collection_visible(LOOP_COLLECTION_NAME, True)
    _set_collection_visible(SOURCE_COLLECTION_NAME, False, create=False)

    indices = _object_indices()
    plane_material = _plane_material()
    loop_material = _loop_material()

    for index in indices:
        plane = _plane_object_for_index(index)
        loop = _loop_object_for_index(index)
        _reset_plane_geometry(plane)
        _reset_loop_geometry(loop)
        _assign_material(plane.data, plane_material)
        _assign_material(loop.data, loop_material)
        _park_object(plane)
        _park_object(loop)

        proxy = bpy.data.objects.get(f"{PROXY_OBJECT_PREFIX}_{index:02d}")
        if proxy is not None:
            _ensure_linked_to_collection(proxy, PROXY_COLLECTION_NAME)
            _park_object(proxy)

    for obj in _objects_in_collection(PROXY_COLLECTION_NAME):
        _park_object(obj)
    for obj in _existing_dynamic_visuals():
        _park_object(obj)

    print(
        f"Prepared indices={indices}; parked proxy/plane/loop visuals at "
        f"{PARK_LOCATION}; hi-res collection hidden"
    )


def _object_indices() -> list[int]:
    source_collection = bpy.data.collections[SOURCE_COLLECTION_NAME]
    source_count = sum(1 for obj in source_collection.all_objects if obj.type == "MESH")
    indices = set(range(source_count))

    prefix = PROXY_OBJECT_PREFIX + "_"
    for obj in _objects_in_collection(PROXY_COLLECTION_NAME):
        if not obj.name.startswith(prefix):
            continue
        suffix = obj.name[len(prefix) : len(prefix) + 2]
        try:
            indices.add(int(suffix))
        except ValueError:
            pass

    return sorted(indices)


def _plane_object_for_index(index: int) -> bpy.types.Object:
    name = f"{PLANE_OBJECT_PREFIX}_{index:02d}"
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.type != "MESH":
        _rename_bad_object(existing)
        existing = None
    if existing is None:
        mesh = bpy.data.meshes.new(name + " Mesh")
        existing = bpy.data.objects.new(name, mesh)
    _ensure_linked_to_collection(existing, PLANE_COLLECTION_NAME)
    return existing


def _loop_object_for_index(index: int) -> bpy.types.Object:
    name = f"{LOOP_OBJECT_PREFIX}_{index:02d}"
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.type != "CURVE":
        _rename_bad_object(existing)
        existing = None
    if existing is None:
        curve = bpy.data.curves.new(name + " Curve", "CURVE")
        curve.dimensions = "3D"
        existing = bpy.data.objects.new(name, curve)
    _ensure_linked_to_collection(existing, LOOP_COLLECTION_NAME)
    return existing


def _existing_dynamic_visuals() -> list[bpy.types.Object]:
    prefixes = (PLANE_OBJECT_PREFIX + "_", LOOP_OBJECT_PREFIX + "_")
    return [
        obj
        for obj in bpy.data.objects
        if obj.name.startswith(prefixes) and "_WrongType_" not in obj.name
    ]


def _objects_in_collection(name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(name)
    if collection is None:
        return []
    return list(collection.all_objects)


def _collection_named(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    _link_collection_to_scene(collection)
    return collection


def _ensure_linked_to_collection(obj: bpy.types.Object, collection_name: str) -> None:
    collection = _collection_named(collection_name)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def _rename_bad_object(obj: bpy.types.Object) -> None:
    base = obj.name
    suffix = 1
    while bpy.data.objects.get(f"{base}_WrongType_{suffix:02d}") is not None:
        suffix += 1
    obj.name = f"{base}_WrongType_{suffix:02d}"
    obj.location = PARK_LOCATION
    obj.hide_viewport = True
    obj.hide_render = True
    try:
        obj.hide_set(True)
    except RuntimeError:
        pass


def _set_collection_visible(name: str, visible: bool, create: bool = True) -> None:
    collection = _collection_named(name) if create else bpy.data.collections.get(name)
    if collection is None:
        return

    if visible:
        _link_collection_to_scene(collection)
    collection.hide_viewport = not visible
    collection.hide_render = not visible

    layer_collection = _layer_collection_for(collection)
    if layer_collection is not None:
        layer_collection.exclude = not visible
        layer_collection.hide_viewport = not visible


def _link_collection_to_scene(collection: bpy.types.Collection) -> None:
    scene_children = bpy.context.scene.collection.children
    if collection.name in {child.name for child in scene_children}:
        return
    try:
        scene_children.link(collection)
    except RuntimeError:
        pass


def _layer_collection_for(
    collection: bpy.types.Collection,
    layer_collection: bpy.types.LayerCollection | None = None,
) -> bpy.types.LayerCollection | None:
    if layer_collection is None:
        layer_collection = bpy.context.view_layer.layer_collection
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = _layer_collection_for(collection, child)
        if found is not None:
            return found
    return None


def _reset_plane_geometry(obj: bpy.types.Object) -> None:
    half = PLANE_SIZE * 0.5
    obj.data.clear_geometry()
    obj.data.from_pydata(
        [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    obj.data.update()


def _reset_loop_geometry(obj: bpy.types.Object) -> None:
    curve = obj.data
    while curve.splines:
        curve.splines.remove(curve.splines[0])

    half = PLANE_SIZE * 0.35
    spline = curve.splines.new("POLY")
    spline.points.add(3)
    coords = [
        (-half, -half, 0.0, 1.0),
        (half, -half, 0.0, 1.0),
        (half, half, 0.0, 1.0),
        (-half, half, 0.0, 1.0),
    ]
    for point, co in zip(spline.points, coords):
        point.co = co
    spline.use_cyclic_u = True

    curve.dimensions = "3D"
    curve.bevel_depth = LOOP_BEVEL_DEPTH
    curve.bevel_resolution = 3


def _plane_material() -> bpy.types.Material:
    material = bpy.data.materials.get(PLANE_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(PLANE_MATERIAL_NAME)
    material.diffuse_color = (0.1, 0.55, 1.0, PLANE_ALPHA)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Alpha"].default_value = PLANE_ALPHA
    material.blend_method = "BLEND"
    material.use_screen_refraction = True
    material.show_transparent_back = True
    return material


def _loop_material() -> bpy.types.Material:
    material = bpy.data.materials.get(LOOP_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(LOOP_MATERIAL_NAME)
    material.diffuse_color = (1.0, 0.12, 0.04, 1.0)
    return material


def _assign_material(data: bpy.types.ID, material: bpy.types.Material) -> None:
    if data.materials:
        data.materials[0] = material
    else:
        data.materials.append(material)


def _park_object(obj: bpy.types.Object) -> None:
    obj.location = PARK_LOCATION
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.hide_viewport = False
    obj.hide_render = False
    try:
        obj.hide_set(False)
    except RuntimeError:
        pass
    obj.update_tag()


if __name__ == "__main__":
    main()