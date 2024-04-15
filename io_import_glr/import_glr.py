import os
import struct
import bpy
import bmesh
import hashlib
from .shader import setup_n64_material
from .utils import (
    get_texture_filter,
    get_texture_wrap_mode,
    get_backface_culling,
    decode_combiner_mode,
    decode_blender_mode,
)


### Import Plugin Entry Point
def load(context, **keywords):
    if keywords['files']:
        files = [file.name for file in keywords['files']]
    else:
        files = [keywords['filepath']]

    if files[0] == '':
        raise RuntimeError('No .glr files have been selected for import!')

    filter_list = parse_filter_list(keywords['filter_list'])
    dir_name = os.path.dirname(keywords['filepath'])
    triangle_options = {
        k: keywords[k] for k in [
            'enable_mat_transparency',
            'enable_bf_culling',
            'enable_fog',
            'filter_mode',
        ]
    }
    triangle_options['filter_list'] = filter_list

    # Deselect everything; after import, only imported objects will be
    # selected
    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action='DESELECT')

    for glr_file in files:
        filepath = os.path.join(dir_name, glr_file)
        ob = load_glr(filepath, **triangle_options)

        context.scene.collection.objects.link(ob)

        ob.select_set(True)

        ob.location = context.scene.cursor.location
        ob.location += keywords['move']
        ob.rotation_euler = keywords['rotation']
        ob.scale = keywords['scale']

        if keywords['merge_doubles']:
            ob_mesh = ob.data
            bm = bmesh.new()
            bm.from_mesh(ob_mesh)
            merge_distance = round(keywords['merge_distance'], 6)  # chopping off extra precision
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_distance)
            bm.to_mesh(ob_mesh)
            bm.free()

    # Make the last object active
    context.view_layer.objects.active = ob

    # Checking and enabling Color Management options
    if keywords['enable_srgb']:
        context.scene.display_settings.display_device = 'sRGB'
        context.scene.view_settings.view_transform = 'Standard'
        context.scene.sequencer_colorspace_settings.name = 'sRGB'

    return {'FINISHED'}


def parse_filter_list(filter_str):
    filter_list = set()

    if filter_str:
        for x in filter_str.split(','):
            try:
                x = 0 if x == 'NO_TEXTURE' else int(x, 16)
            except ValueError:
                raise ValueError('Invalid value in filter list:', x)
            filter_list.add(x)

    return filter_list


def load_glr(filepath, **triangle_options):
    texture_dir = os.path.abspath(os.path.dirname(filepath))
    with open(filepath, 'rb') as fb:
        return GlrImporter(fb, texture_dir, **triangle_options).load()


class GlrImporter:
    def __init__(
        self,
        fb,
        texture_dir,
        enable_mat_transparency=True,
        enable_bf_culling=False,
        enable_fog=True,
        filter_mode=True,
        filter_list='',
    ):
        if isinstance(filter_list, str):
            filter_list = parse_filter_list(filter_list)

        self.fb = fb
        self.texture_dir = texture_dir
        self.show_alpha = enable_mat_transparency
        self.display_culling = enable_bf_culling
        self.filter_mode = filter_mode
        self.filter_list = filter_list
        self.enable_fog = enable_fog
        self.obj_name = None
        self.num_tris = None
        self.microcode = None

    def load(self):
        self.load_header()
        return self.do_tris()

    def load_header(self):
        fb = self.fb

        # Check magic
        if fb.read(6) != b'GL64R\0':
            raise RuntimeError('Not a valid glr file')

        # Check version
        version = struct.unpack('<H', fb.read(2))[0]
        if version > 0 and version < 3:
            raise RuntimeError(f'Outdated glr file format detected ({version}), please update the glr import addon')
        elif version != 3:
            raise RuntimeError(f'Unknown N64 Ripper version ({version}) encountered')

        romname = fb.read(20)
        romname = romname.decode(errors='replace')
        romname = romname.replace('\0', '').strip()
        romname = romname or 'Unknown N64 Game'
        self.obj_name = romname + ' (' + os.path.basename(fb.name)[:-4] + ')'

        self.num_tris = struct.unpack('<I', fb.read(4))[0]
        self.microcode = struct.unpack('<I', fb.read(4))[0]

    def do_tris(self):
        fb = self.fb

        verts = []
        faces = []
        shade_colors = []
        prim_colors = []
        env_colors = []
        blend_colors = []
        fog_colors = []
        fog_levels = []
        uvs0 = []
        uvs1 = []

        matinfo_cache = {}
        face_materials = []

        for i in range(self.num_tris):
            # Read vertices
            tri_verts = [fb.read(44) for _ in range(3)]

            # Read triangle data
            (
                fog_r, fog_g, fog_b, fog_a,
                blend_r, blend_g, blend_b, blend_a,
                env_r, env_g, env_b, env_a,
                prim_r, prim_g, prim_b, prim_a,
                prim_l, prim_m,
                fog_multiplier, fog_offset,
                k4, k5,
                combiner_mux,
                other_mode,
                geometry_mode,
                tex0_crc,
                tex0_maskS, tex0_maskT,
                tex0_wrapS, tex0_wrapT,
                tex1_crc,
                tex1_maskS, tex1_maskT,
                tex1_wrapS, tex1_wrapT,
            ) = struct.unpack('<4f4f4f4f2f2f2iQQIQ4BQ4B', fb.read(132))

            # Skip tris blacklisted by their texture CRC
            blacklisted = tex0_crc in self.filter_list
            if not self.filter_mode:  # Whitelist mode
                blacklisted = not blacklisted
            if blacklisted:
                continue

            # Process vertices
            for vert in tri_verts:
                (
                    x, y, z, r, g, b, a, s0, t0, s1, t1,
                ) = struct.unpack('<11f', vert)

                shade_colors += [r, g, b, a]
                uvs0 += [s0, t0]
                uvs1 += [s1, t1]
                verts.append((x, -z, y))  # Yup2Zup

                # When fog enabled, alpha is the fog level
                fog_levels.append(a if geometry_mode & 0x10000 else 0)

            # Store per-tri colors as vertex colors (once per corner)
            prim_colors += [prim_r, prim_g, prim_b, prim_a] * 3
            env_colors += [env_r, env_g, env_b, env_a] * 3
            blend_colors += [blend_r, blend_g, blend_b, blend_a] * 3
            fog_colors += [fog_r, fog_g, fog_b, fog_a] * 3

            faces.append((len(verts) - 3, len(verts) - 2, len(verts) - 1))

            # Gather all the info we need to make the material for this tri
            matinfo = (
                combiner_mux,
                other_mode,
                geometry_mode,
                tex0_crc,
                tex0_wrapS, tex0_wrapT,
                tex1_crc,
                tex1_wrapS, tex1_wrapT,
            )
            material_index = matinfo_cache.setdefault(matinfo, len(matinfo_cache))
            face_materials.append(material_index)

        # Create mesh
        mesh = bpy.data.meshes.new(self.obj_name)
        mesh.from_pydata(verts, [], faces)

        # Create & assign materials
        for matinfo in matinfo_cache:
            mesh.materials.append(self.create_material(matinfo))
        mesh.polygons.foreach_set('material_index', face_materials)

        # Create attributes

        mesh.vertex_colors.new(
            name='Shade Color'
        ).data.foreach_set('color', shade_colors)

        mesh.vertex_colors.new(
            name='Primitive Color',
        ).data.foreach_set('color', prim_colors)

        mesh.vertex_colors.new(
            name='Env Color',
        ).data.foreach_set('color', env_colors)

        mesh.vertex_colors.new(
            name='Blend Color',
        ).data.foreach_set('color', blend_colors)

        mesh.vertex_colors.new(
            name='Fog Color',
        ).data.foreach_set('color', fog_colors)

        mesh.uv_layers.new(name='UV0').data.foreach_set('uv', uvs0)
        mesh.uv_layers.new(name='UV1').data.foreach_set('uv', uvs1)

        if self.enable_fog and any(fog_levels):
            mesh.attributes.new(
                name='Fog Level', type='FLOAT', domain='POINT',
            ).data.foreach_set('value', fog_levels)

        mesh.validate()

        # Create object
        ob = bpy.data.objects.new(mesh.name, mesh)

        return ob

    def create_material(self, matinfo):
        (
            combiner_mux,
            other_mode,
            geometry_mode,
            tex0_crc,
            tex0_wrapS, tex0_wrapT,
            tex1_crc,
            tex1_wrapS, tex1_wrapT,
        ) = matinfo

        cycle_type = (other_mode >> 52) & 0x3
        two_cycle_mode = cycle_type == 1  # 0 = 1CYCLE, 1 = 2CYCLE

        combiner1, combiner2 = decode_combiner_mode(combiner_mux)
        blender1, blender2 = decode_blender_mode(other_mode)

        # When fog is enabled, Fog Level should be used instead
        # of the Shade Alpha
        if geometry_mode & 0x10000:
            combiner1 = tuple('Fog Level' if s == 'Shade Alpha' else s for s in combiner1)
            combiner2 = tuple('Fog Level' if s == 'Shade Alpha' else s for s in combiner2)
            blender1 = tuple('Fog Level' if s == 'Shade Alpha' else s for s in blender1)
            blender2 = tuple('Fog Level' if s == 'Shade Alpha' else s for s in blender2)

        if not two_cycle_mode:
            combiner2 = blender2 = None

        tex0, tex1 = [
            {
                'crc': crc,
                'filter': get_texture_filter(other_mode),
                'wrapS': get_texture_wrap_mode(wrapS),
                'wrapT': get_texture_wrap_mode(wrapT),
            }
            for crc, wrapS, wrapT in [
                (tex0_crc, tex0_wrapS, tex0_wrapT),
                (tex1_crc, tex1_wrapS, tex1_wrapT),
            ]
        ]
        tex0['uv_map'] = 'UV0'
        tex1['uv_map'] = 'UV1'

        cull_backface = get_backface_culling(geometry_mode, self.microcode)
        cull_backface &= self.display_culling

        args = (
            combiner1, combiner2,
            blender1, blender2,
            tex0, tex1,
            cull_backface,
            self.show_alpha,
        )

        mat_hash = hashlib.sha256(str(args).encode()).hexdigest()[:16]
        mat_name = f'N64 Shader {mat_hash}'

        found_mat_index = bpy.data.materials.find(mat_name)

        if found_mat_index != -1:
            mat = bpy.data.materials[found_mat_index]
        else:
            mat = bpy.data.materials.new(mat_name)
            setup_n64_material(mat, self.texture_dir, *args)

        return mat
