import os
import struct
import bpy
import bmesh
import hashlib
from .utils import (
    get_texture_filter,
    get_texture_wrap_mode,
    get_backface_culling,
    show_combiner_formula,
    show_blender_formula,
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


# Imported materials are supposed to perform (highly simplified) high
# level emulation of the N64's RDP pixel shader pipeline.
#
# OVERVIEW OF THE RDP
#
#   ┌───────────┐
#   │ Texture 0 ├──┐
#   └───────────┘  │
#   ┌───────────┐  └───►┌────────────────┐      ┌─────────┐ Output
#   │ Texture 1 ├──────►│ Color Combiner ├─────►│ Blender ├────────►
#   └───────────┘  ┌───►└────────────────┘ ┌───►└─────────┘
#    Colors, etc.  │                       │
#   ───────────────┴───────────────────────┘
#
# COLOR COMBINER
#
# The color combiner is used for effects like combining the texture and
# shading color. It combines four input variables, a, b, c, d, with the
# formula
#
#   Output = (a - b) * c + d
#
# RGB and Alpha are combined separately. In two-cycle mode the color
# combiner runs twice, and the second run can use the output from the
# first run as one of its inputs. Altogether that's 16 inputs in total.
#
#   (4 variables) * (2 RGB/Alpha) * (2 1st/2nd cycle) = 16
#
# The combiner is configured with a 64-bit mux value that specifies the
# source for the each of the 16 inputs.
#
# BLENDER
#
# The blender is used for effects like alpha blending and fog. Similar
# to the combiner, it combines two RGB colors, p and m, with two
# weights, a and b, using the formula
#
#   Output = (p * a + m * b) / (a + b)
#
# Unlike the combiner, it can use the current pixel in the framebuffer
# as input. It, too, can run in two-cycle mode.
#
# REFERENCES
# http://n64devkit.square7.ch/tutorial/graphics/
# http://n64devkit.square7.ch/pro-man/pro12/index.htm
# https://hack64.net/wiki/doku.php?id=rcpstructs
# Angrylion's RDP Plus


def setup_n64_material(
    mat,
    texture_dir,
    combiner1, combiner2,
    blender1, blender2,
    tex0, tex1,
    cull_backfacing,
    show_alpha,
):
    mat.shadow_method = 'NONE'
    mat.blend_method = 'OPAQUE'
    mat.use_backface_culling = cull_backfacing

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Gather all input sources the RDP will need
    sources = set()
    sources.update(combiner1)
    sources.update(combiner2 or [])
    sources.update(blender1)
    sources.update(blender2 or [])

    # TODO: node positions needs a loooot of work
    x, y = 200, 100

    # Create nodes for the input sources
    input_map = make_rdp_input_nodes(mat, texture_dir, sources, tex0, tex1, location=(-200, 0))

    # 1st Color Combiner cycle

    input_map['Combined Color'] = 1.0
    input_map['Combined Alpha'] = 1.0

    node_comb1 = nodes.new('ShaderNodeGroup')
    node_comb1.width = 220
    node_comb1.location = x, y
    x, y = x + 400, y - 200
    node_comb1.node_tree = get_combiner_group()

    for i in range(8):
        connect_input(mat, input_map[combiner1[i]], node_comb1.inputs[i])

    input_map['Combined Color'] = node_comb1.outputs[0]
    input_map['Combined Alpha'] = node_comb1.outputs[1]

    # 2nd Color Combiner cycle

    # Skip the 2nd cycle if it does nothing; two-cycle mode is probably
    # only enabled for a blender effect.
    if combiner2 == ('0', '0', '0', 'Combined Color', '0', '0', '0', 'Combined Alpha'):
        combiner2 = None

    if combiner2:
        node_comb2 = nodes.new('ShaderNodeGroup')
        node_comb2.width = 220
        node_comb2.location = x, y
        x, y = x + 400, y - 200
        node_comb2.node_tree = get_combiner_group()

        for i in range(8):
            connect_input(mat, input_map[combiner2[i]], node_comb2.inputs[i])

        input_map['Combined Color'] = node_comb2.outputs[0]
        input_map['Combined Alpha'] = node_comb2.outputs[1]

    # Next the blender
    # It's poorly implemented atm...

    x, y = x + 200, y - 100

    # Handle some cases where the blender formula is simple
    # (fog, in particular)
    node_blnd1 = make_simple_blender_lerp_node(mat, blender1, input_map)
    if node_blnd1:
        node_blnd1.location = x, y
        x, y = x + 200, y - 100
    if blender2:
        node_blnd2 = make_simple_blender_lerp_node(mat, blender2, input_map)
        if node_blnd2:
            node_blnd2.location = x, y
            x, y = x + 200, y - 100

    # If the last step of the blender reads the framebuffer color at
    # all, we crudely assume it's doing alpha blending
    last_blender = blender2 or blender1  # whichever comes last
    if 'Framebuffer Color' in last_blender:
        node_mixtr = nodes.new('ShaderNodeMixShader')
        node_trans = nodes.new('ShaderNodeBsdfTransparent')

        connect_input(mat, input_map['Combined Alpha'], node_mixtr.inputs[0])
        connect_input(mat, node_trans.outputs[0], node_mixtr.inputs[1])
        connect_input(mat, input_map['Combined Color'], node_mixtr.inputs[2])

        node_trans.location = x, y - 100
        node_mixtr.location = x + 200, y
        x, y = x + 500, y

        input_map['Combined Color'] = node_mixtr.outputs[0]

        if show_alpha:
            mat.blend_method = 'HASHED'

    # TODO: alpha compare

    node_out = nodes.new('ShaderNodeOutputMaterial')
    node_out.location = x, y
    links.new(input_map['Combined Color'], node_out.inputs[0])

    # Custom props (useful for debugging)
    mat['N64 Texture 0'] = show_texture_info(tex0)
    mat['N64 Texture 1'] = show_texture_info(tex1)
    mat['N64 Color Combiner 1'] = show_combiner_formula(*combiner1[:4])
    mat['N64 Alpha Combiner 1'] = show_combiner_formula(*combiner1[4:])
    mat['N64 Color Combiner 2'] = show_combiner_formula(*combiner2[:4]) if combiner2 else ''
    mat['N64 Alpha Combiner 2'] = show_combiner_formula(*combiner2[4:]) if combiner2 else ''
    mat['N64 Blender 1'] = show_blender_formula(*blender1)
    mat['N64 Blender 2'] = show_blender_formula(*blender2) if blender2 else ''


def show_texture_info(tex):
    crc = tex['crc']
    tfilter = tex['filter']
    wrapS = tex['wrapS']
    wrapT = tex['wrapT']

    wrap = wrapS if wrapS == wrapT else f'{wrapS} x {wrapT}'

    return f'{crc:016X}, {tfilter}, {wrap}'


def connect_input(mat, input, socket):
    # Connects input -> socket
    # Input can be either an output socket or a constant

    if isinstance(input, (int, float)):
        if socket.type == 'RGBA':
            socket.default_value = (input, input, input, 1.0)
        else:
            socket.default_value = input
    else:
        mat.node_tree.links.new(input, socket)


def make_rdp_input_nodes(mat, texture_dir, sources, tex0, tex1, location):
    # Given a list of input sources, creates the nodes needed to supply
    # those inputs. Returns a mapping from input source names to the
    # socket (or constant) you should use for that input.

    input_map = {
        '0': 0.0,
        '1': 1.0,
    }
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    x, y = location

    # Texture inputs
    for i in range(2):
        tex = tex0 if i == 0 else tex1
        if f'Texel {i} Color' in sources or f'Texel {i} Alpha' in sources:
            node = make_texture_node(mat, texture_dir, tex, i, location=(x, y))
            y -= 300
            input_map[f'Texel {i} Color'] = node.outputs['Color']
            input_map[f'Texel {i} Alpha'] = node.outputs['Alpha']

    # Vertex Color inputs
    for vc in ['Shade', 'Primitive', 'Env', 'Blend', 'Fog']:
        if f'{vc} Color' in sources or f'{vc} Alpha' in sources:
            node = nodes.new('ShaderNodeVertexColor')
            node.location = x, y
            y -= 200
            node.layer_name = f'{vc} Color'
            node.name = node.label = f'{vc} Color'
            input_map[f'{vc} Color'] = node.outputs['Color']
            input_map[f'{vc} Alpha'] = node.outputs['Alpha']

    if 'Fog Level' in sources:
        node = nodes.new('ShaderNodeAttribute')
        node.location = x, y
        y -= 200
        node.attribute_name = 'Fog Level'
        node.name = node.label = 'Fog Level'
        input_map['Fog Level'] = node.outputs['Fac']

    # Not yet implemented
    unimplemented = [
        'Key Center',
        'Key Scale',
        'LOD Fraction',
        'Primitive LOD Fraction',
        'Noise',
        'Convert K4',
        'Convert K5',
    ]
    for un_src in unimplemented:
        if un_src in sources:
            print('Unimplemented color combiner input:', un_src)
            node = nodes.new('ShaderNodeRGB')
            node.location = x, y
            y += 300
            node.outputs[0].default_value = (0.0, 1.0, 1.0, 1.0)
            node.label = f'UNIMPLEMENTED {un_src}'
            input_map[un_src] = node.outputs[0]

    return input_map


def load_image(texture_dir, crc):
    if crc == 0:
        return None

    filepath = os.path.join(texture_dir, f'{crc:016X}.png')
    try:
        image = bpy.data.images.load(filepath, check_existing=True)
    except Exception:
        # Image didn't exist
        # Allow the path to be resolved later
        image = bpy.data.images.new(os.path.basename(filepath), 16, 16)
        image.filepath = filepath
        image.source = 'FILE'
    return image


def make_texture_node(mat, texture_dir, tex, tex_num, location):
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    x, y = location

    # Image Texture node
    node_tex = nodes.new('ShaderNodeTexImage')
    node_tex.name = node_tex.label = 'Texture 0' if tex_num == 0 else 'Texture 1'
    node_tex.width = 290
    node_tex.location = x - 150, y
    node_tex.image = load_image(texture_dir, tex['crc'])
    node_tex.interpolation = tex['filter']
    uv_socket = node_tex.inputs[0]

    x -= 370

    # Wrapping
    wrapS, wrapT = tex['wrapS'], tex['wrapT']
    if wrapS == wrapT == 'Repeat':
        node_tex.extension = 'REPEAT'
    elif wrapS == wrapT == 'Clamp':
        node_tex.extension = 'EXTEND'
    else:
        # Use math nodes to emulate other wrap modes

        node_tex.extension = 'EXTEND'

        frame = nodes.new('NodeFrame')
        frame.label = f'{wrapS} ({wrapS[0]}) x {wrapT} ({wrapT[0]})'

        # Combine XYZ
        node_com = nodes.new('ShaderNodeCombineXYZ')
        node_com.parent = frame
        node_com.location = x - 80, y - 110
        links.new(uv_socket, node_com.outputs[0])
        u_socket = node_com.inputs[0]
        v_socket = node_com.inputs[1]

        x -= 120

        for i in [0, 1]:
            wrap = wrapS if i == 0 else wrapT
            socket = node_com.inputs[i]

            if wrap == 'Repeat':
                node_math = nodes.new('ShaderNodeMath')
                node_math.parent = frame
                node_math.location = x - 140, y + 30 - i*200
                node_math.operation = 'WRAP'
                node_math.inputs[1].default_value = 0
                node_math.inputs[2].default_value = 1
                links.new(socket, node_math.outputs[0])
                socket = node_math.inputs[0]

            elif wrap == 'Mirror':
                node_math = nodes.new('ShaderNodeMath')
                node_math.parent = frame
                node_math.location = x - 140, y + 30 - i*200
                node_math.operation = 'PINGPONG'
                node_math.inputs[1].default_value = 1
                links.new(socket, node_math.outputs[0])
                socket = node_math.inputs[0]

            else:
                # Clamp doesn't require a node since the default on the
                # Texture node is EXTEND.
                # Adjust node location for aesthetics though.
                if i == 0:
                    node_com.location[1] += 90

            if i == 0:
                u_socket = socket
            else:
                v_socket = socket

        x -= 180

        # Separate XYZ
        node_sep = nodes.new('ShaderNodeSeparateXYZ')
        node_sep.parent = frame
        node_sep.location = x - 140, y - 100
        links.new(u_socket, node_sep.outputs[0])
        links.new(v_socket, node_sep.outputs[1])
        uv_socket = node_sep.inputs[0]

        x -= 180

    # UVMap node
    node_uv = nodes.new('ShaderNodeUVMap')
    node_uv.name = node_uv.label = 'UV Map Texture 0' if tex_num == 0 else 'UV Map Texture 1'
    node_uv.location = x - 160, y - 70
    node_uv.uv_map = tex['uv_map']
    links.new(uv_socket, node_uv.outputs[0])

    return node_tex


def make_simple_blender_lerp_node(mat, blender, input_map):
    # Creates a node for the blender in the simple case when it can be
    # implemented by a MixRGB (lerp) node. Returns the node if created,
    # or None if the blender wasn't simple enough.

    p, a, m, b = blender

    # It's simple if...
    is_simple = (
        # (p*a + m*(1-a))/(a + (1-a)) = lerp(m, p, a)
        b == 'One Minus A' and
        # Reading from framebuffer is not required
        p != 'Framebuffer Color' and m != 'Framebuffer Color'
    )
    if not is_simple:
        return None

    node_mix = mat.node_tree.nodes.new('ShaderNodeMixRGB')
    connect_input(mat, input_map[a], node_mix.inputs[0])
    connect_input(mat, input_map[m], node_mix.inputs[1])
    connect_input(mat, input_map[p], node_mix.inputs[2])
    input_map['Combined Color'] = node_mix.outputs[0]

    return node_mix


def get_combiner_group():
    if 'RDP Color Combiner' not in bpy.data.node_groups:
        create_combiner_group()
    return bpy.data.node_groups['RDP Color Combiner']


def create_combiner_group():
    # Creates a node group with 8 inputs and 2 outputs that performs one
    # cycle of the color combiner.
    #
    #   Output Color = (Color A - Color B) * Color C + Color D
    #   Output Alpha = (Alpha A - Alpha B) * Alpha C + Alpha D
    #
    # NOTE: The color math is currently being done in linear space,
    # should be sRGB?

    group = bpy.data.node_groups.new('RDP Color Combiner', 'ShaderNodeTree')
    nodes = group.nodes
    links = group.links

    group.inputs.new('NodeSocketColor', 'Color A')
    group.inputs.new('NodeSocketColor', 'Color B')
    group.inputs.new('NodeSocketColor', 'Color C')
    group.inputs.new('NodeSocketColor', 'Color D')
    group.inputs.new('NodeSocketFloat', 'Alpha A')
    group.inputs.new('NodeSocketFloat', 'Alpha B')
    group.inputs.new('NodeSocketFloat', 'Alpha C')
    group.inputs.new('NodeSocketFloat', 'Alpha D')
    group.outputs.new('NodeSocketColor', 'Color')
    group.outputs.new('NodeSocketFloat', 'Alpha')

    node_input = nodes.new('NodeGroupInput')
    node_subc = nodes.new('ShaderNodeMixRGB')
    node_mulc = nodes.new('ShaderNodeMixRGB')
    node_addc = nodes.new('ShaderNodeMixRGB')
    node_suba = nodes.new('ShaderNodeMath')
    node_mula = nodes.new('ShaderNodeMath')
    node_adda = nodes.new('ShaderNodeMath')
    node_output = nodes.new('NodeGroupOutput')

    node_subc.blend_type = node_suba.operation = 'SUBTRACT'
    node_mulc.blend_type = node_mula.operation = 'MULTIPLY'
    node_addc.blend_type = node_adda.operation = 'ADD'
    node_subc.inputs[0].default_value = 1.0
    node_mulc.inputs[0].default_value = 1.0
    node_addc.inputs[0].default_value = 1.0

    links.new(node_input.outputs[0], node_subc.inputs[1])
    links.new(node_input.outputs[1], node_subc.inputs[2])
    links.new(node_input.outputs[2], node_mulc.inputs[2])
    links.new(node_input.outputs[3], node_addc.inputs[2])
    links.new(node_input.outputs[4], node_suba.inputs[0])
    links.new(node_input.outputs[5], node_suba.inputs[1])
    links.new(node_input.outputs[6], node_mula.inputs[1])
    links.new(node_input.outputs[7], node_adda.inputs[1])
    links.new(node_subc.outputs[0], node_mulc.inputs[1])
    links.new(node_mulc.outputs[0], node_addc.inputs[1])
    links.new(node_suba.outputs[0], node_mula.inputs[0])
    links.new(node_mula.outputs[0], node_adda.inputs[0])
    links.new(node_addc.outputs[0], node_output.inputs[0])
    links.new(node_adda.outputs[0], node_output.inputs[1])

    node_input.location = -555, -201
    node_subc.location = -200, 219
    node_mulc.location = -15, 164
    node_addc.location = 178, 129
    node_suba.location = -150, -252
    node_mula.location = 43, -355
    node_adda.location = 247, -548
    node_output.location = 598, -113

    return group
