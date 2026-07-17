import bpy


COLLECTION_PREFIX = "Collection"
COLLECTION_COUNT = 7
GRID_COLUMNS = 5
GRID_ROWS = 4
X_SPACING = 2.0
Y_SPACING = 2.4
SET_Z_TO_ZERO = True


def sorted_mesh_objects(collection):
    objects = [obj for obj in collection.objects if obj.type == "MESH"]
    return sorted(objects, key=lambda obj: obj.name.lower())


def collection_name(collection_index):
    return f"{COLLECTION_PREFIX} {collection_index}"


def collect_objects():
    objects = []

    for collection_index in range(1, COLLECTION_COUNT + 1):
        name = collection_name(collection_index)
        collection = bpy.data.collections.get(name)

        if collection is None:
            print(f"Skipping {name}: collection not found")
            continue

        for obj in sorted_mesh_objects(collection):
            objects.append(obj)

    return objects


def grid_positions():
    x_offset = (GRID_COLUMNS - 1) * X_SPACING * 0.5
    y_offset = (GRID_ROWS - 1) * Y_SPACING * 0.5
    positions = []

    for row in range(GRID_ROWS):
        y = y_offset - (row * Y_SPACING)
        for column in range(GRID_COLUMNS):
            x = (column * X_SPACING) - x_offset
            positions.append((x, y))

    return positions


def arrange_grid(objects):
    positions = grid_positions()
    print(f"Arranging {len(objects)} objects in a {GRID_COLUMNS}x{GRID_ROWS} centered grid")
    print(f"  X spacing: {X_SPACING:.3f} m")
    print(f"  Y spacing: {Y_SPACING:.3f} m")

    if len(objects) > len(positions):
        print(f"  warning: {len(objects)} objects but only {len(positions)} grid positions")

    for index, obj in enumerate(objects):
        if index >= len(positions):
            print(f"    skipping extra object {obj.name}")
            continue

        x, y = positions[index]
        obj.location.x = x
        obj.location.y = y
        if SET_Z_TO_ZERO:
            obj.location.z = 0.0

        print(f"    {index:02d}: {obj.name} -> x={x:.3f}, y={y:.3f}, z={obj.location.z:.3f}")


def main():
    print("Starting centered grid collection arrangement")

    objects = collect_objects()
    arrange_grid(objects)

    print("Finished collection arrangement")


main()
