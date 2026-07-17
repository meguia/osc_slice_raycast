import bpy
from mathutils import Matrix, Vector


COLLECTION_NAME = "Collection"
TARGET_LENGTH_METERS = 2.0


def vector_bounds(coords):
    if not coords:
        return None

    min_corner = Vector((min(v.x for v in coords), min(v.y for v in coords), min(v.z for v in coords)))
    max_corner = Vector((max(v.x for v in coords), max(v.y for v in coords), max(v.z for v in coords)))
    center = (min_corner + max_corner) * 0.5
    size = max_corner - min_corner
    return center, size


def geometric_center(coords):
    center = Vector((0.0, 0.0, 0.0))
    for coord in coords:
        center += coord
    return center / len(coords)


def covariance_matrix(coords, center):
    matrix = Matrix(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))

    for coord in coords:
        offset = coord - center
        matrix[0][0] += offset.x * offset.x
        matrix[0][1] += offset.x * offset.y
        matrix[0][2] += offset.x * offset.z
        matrix[1][0] += offset.y * offset.x
        matrix[1][1] += offset.y * offset.y
        matrix[1][2] += offset.y * offset.z
        matrix[2][0] += offset.z * offset.x
        matrix[2][1] += offset.z * offset.y
        matrix[2][2] += offset.z * offset.z

    return matrix * (1.0 / len(coords))


def largest_axis(coords):
    center = geometric_center(coords)
    covariance = covariance_matrix(coords, center)
    axis = Vector((1.0, 0.0, 0.0))

    # Power iteration finds the direction where the vertices spread the most.
    for _ in range(32):
        next_axis = covariance @ axis
        if next_axis.length == 0:
            return Vector((0.0, 1.0, 0.0))
        axis = next_axis.normalized()

    if axis.y < 0:
        axis.negate()

    return axis


def normalize_object(obj):
    if obj.type != "MESH" or not obj.data.vertices:
        print(f"Skipping {obj.name}: not a mesh or empty")
        return

    if obj.data.users > 1:
        obj.data = obj.data.copy()

    mesh = obj.data

    print(f"Normalizing {obj.name}...")

    # Bake the current world transform into the mesh, then reset the object.
    world_matrix = obj.matrix_world.copy()
    for vertex in mesh.vertices:
        vertex.co = world_matrix @ vertex.co

    obj.matrix_world = Matrix.Identity(4)

    coords = [vertex.co.copy() for vertex in mesh.vertices]
    center = geometric_center(coords)
    axis = largest_axis(coords)
    print(f"  largest axis: ({axis.x:.4f}, {axis.y:.4f}, {axis.z:.4f})")

    # Rotate first: align the principal axis with Blender's Y axis.
    rotation = axis.rotation_difference(Vector((0.0, 1.0, 0.0))).to_matrix().to_4x4()
    for vertex in mesh.vertices:
        vertex.co = rotation @ (vertex.co - center)

    coords = [vertex.co.copy() for vertex in mesh.vertices]
    min_y = min(coord.y for coord in coords)
    max_y = max(coord.y for coord in coords)
    y_length = max_y - min_y

    if y_length == 0:
        print(f"  skipping scale: Y length is zero")
        return

    # Scale uniformly. No more rotation happens after this point.
    scale = TARGET_LENGTH_METERS / y_length
    for vertex in mesh.vertices:
        vertex.co *= scale

    # Recenter after scaling so the Y span is exactly -1 to 1 meters.
    coords = [vertex.co.copy() for vertex in mesh.vertices]
    bounds = vector_bounds(coords)
    if bounds is None:
        return

    center, size = bounds
    for vertex in mesh.vertices:
        vertex.co.x -= center.x
        vertex.co.y -= center.y
        vertex.co.z -= center.z

    mesh.update()
    print(f"  original Y span after alignment: {y_length:.4f} m")
    print(f"  uniform scale: {scale:.4f}")
    print(f"  final Y span: {-TARGET_LENGTH_METERS / 2:.4f} to {TARGET_LENGTH_METERS / 2:.4f} m")
    print(f"Finished {obj.name}")


def main():
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        raise RuntimeError(f'Could not find collection named "{COLLECTION_NAME}"')

    for obj in collection.all_objects:
        normalize_object(obj)


main()
