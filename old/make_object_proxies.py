import bpy


SOURCE_COLLECTION_NAME = "Objects"
PROXY_COLLECTION_NAME = "Object_Proxy"
PROXY_NAME_PREFIX = "SliceObjectProxy"

VOXEL_SIZE_METERS = 0.02
SMOOTH_FACTOR = 1.0
SMOOTH_REPEAT = 2
REPLACE_EXISTING_PROXIES = True


def source_objects():
    collection = bpy.data.collections.get(SOURCE_COLLECTION_NAME)
    if collection is None:
        raise RuntimeError(f'Could not find collection named "{SOURCE_COLLECTION_NAME}"')

    return sorted(
        (obj for obj in collection.all_objects if obj.type == "MESH"),
        key=lambda obj: obj.name.lower(),
    )


def proxy_collection():
    collection = bpy.data.collections.get(PROXY_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(PROXY_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    return collection


def remove_existing_proxy(name):
    if not REPLACE_EXISTING_PROXIES:
        return

    existing = bpy.data.objects.get(name)
    if existing is None:
        return

    mesh = existing.data if existing.type == "MESH" else None
    bpy.data.objects.remove(existing, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def make_proxy_object(source, name, collection):
    remove_existing_proxy(name)

    proxy = source.copy()
    proxy.data = source.data.copy()
    proxy.animation_data_clear()
    proxy.name = name
    proxy.data.name = f"{name} Mesh"
    proxy["proxy_source"] = source.name_full

    collection.objects.link(proxy)
    return proxy


def activate_object(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_modifier(obj, modifier):
    activate_object(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def add_and_apply_voxel_remesh(obj):
    modifier = obj.modifiers.new("Proxy Voxel Remesh", "REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = VOXEL_SIZE_METERS
    modifier.adaptivity = 0.0
    apply_modifier(obj, modifier)


def add_and_apply_smooth(obj):
    modifier = obj.modifiers.new("Proxy Smooth", "SMOOTH")
    modifier.factor = SMOOTH_FACTOR
    modifier.iterations = SMOOTH_REPEAT
    apply_modifier(obj, modifier)


def shade_smooth(obj):
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.update()


def create_proxy(index, source, collection):
    name = f"{PROXY_NAME_PREFIX}_{index:02d}"
    proxy = make_proxy_object(source, name, collection)
    proxy["slice_index"] = index

    add_and_apply_voxel_remesh(proxy)
    add_and_apply_smooth(proxy)
    shade_smooth(proxy)

    print(
        f"{source.name} -> {proxy.name}: "
        f"{len(source.data.vertices)} verts to {len(proxy.data.vertices)} verts, "
        f"{len(source.data.polygons)} faces to {len(proxy.data.polygons)} faces"
    )


def main():
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")

    objects = source_objects()
    collection = proxy_collection()

    for index, source in enumerate(objects):
        create_proxy(index, source, collection)

    print(
        f'Created {len(objects)} proxies in "{PROXY_COLLECTION_NAME}" '
        f"with voxel size {VOXEL_SIZE_METERS} m, smooth factor {SMOOTH_FACTOR}, "
        f"repeat {SMOOTH_REPEAT}."
    )


main()
