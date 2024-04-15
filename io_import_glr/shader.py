import bpy
import os
from .utils import (
    show_combiner_formula,
    show_blender_formula,
)

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


def setup_n64_material(material, texture_dir, *args):
    return N64Shader(material, texture_dir).setup(*args)


class N64Shader:
    def __init__(self, material, texture_dir):
        self.material = material
        self.texture_dir = texture_dir

        material.use_nodes = True
        self.node_tree = material.node_tree
        self.nodes = material.node_tree.nodes
        self.links = material.node_tree.links

        self.use_alpha = False
        self.vars = {'0': 0, '1': 1}

    def setup(
        self,
        combiner1, combiner2,
        blender1, blender2,
        tex0, tex1,
        cull_backfacing,
        show_alpha,
    ):
        mat = self.material

        self.nodes.clear()

        # Gather all input sources the shader needs
        sources = []
        sources += combiner1
        sources += combiner2 or []
        sources += blender1
        sources += blender2 or []

        self.make_inputs(tex0, tex1, sources)
        self.make_combiners(combiner1, combiner2)
        self.make_blenders(blender1, blender2)
        self.make_output()

        # Set material-level properties
        mat.shadow_method = 'NONE'
        mat.use_backface_culling = cull_backfacing
        if self.use_alpha and show_alpha:
            mat.blend_method = 'HASHED'
        else:
            mat.blend_method = 'OPAQUE'

        # Custom props (useful for debugging)
        mat['N64 Texture 0'] = show_texture_info(tex0)
        mat['N64 Texture 1'] = show_texture_info(tex1)
        mat['N64 Color Combiner 1'] = show_combiner_formula(*combiner1[:4])
        mat['N64 Alpha Combiner 1'] = show_combiner_formula(*combiner1[4:])
        mat['N64 Color Combiner 2'] = show_combiner_formula(*combiner2[:4]) if combiner2 else ''
        mat['N64 Alpha Combiner 2'] = show_combiner_formula(*combiner2[4:]) if combiner2 else ''
        mat['N64 Blender 1'] = show_blender_formula(*blender1)
        mat['N64 Blender 2'] = show_blender_formula(*blender2) if blender2 else ''

    def connect(self, v, socket):
        """
        Connect a socket to an input source.

        The input source can be another socket, a constant value, or a
        named variable to take from self.vars.
        """
        if isinstance(v, str):
            v = self.vars[v]

        if isinstance(v, bpy.types.NodeSocket):
            self.links.new(v, socket)
        else:
            if isinstance(v, (int, float)) and socket.type == 'RGBA':
                v = (v, v, v, 1.0)
            socket.default_value = v

    def new_node(self, node_type):
        return self.material.node_tree.nodes.new(node_type)

    def new_color_math_node(self, blend_type):
        """
        Creates a node for color math.

        Returns the node, the two input sockets, and the output socket.
        """
        node = self.new_node('ShaderNodeMix')
        node.data_type = 'RGBA'
        node.blend_type = blend_type
        node.inputs[0].default_value = 1  # Fac
        return node, node.inputs[6], node.inputs[7], node.outputs[2]

    def make_combiners(self, combiner1, combiner2):
        self.make_color_combiner(combiner1[:4], location=(-630, 500))
        self.make_alpha_combiner(combiner1[4:], location=(-630, 0))

        if combiner2:
            self.make_color_combiner(combiner2[:4], location=(250, 500))
            self.make_alpha_combiner(combiner2[4:], location=(250, 0))

    def make_blenders(self, blender1, blender2):
        x, y = 1220, 220

        for blender in [blender1, blender2]:
            if not blender:
                break

            node = self.make_simple_blender_mix_node(blender)
            if node:
                node.location = x, y
                x += 320

            if 'Framebuffer Color' in blender:
                self.use_alpha = True
                break

    def make_output(self):
        # If the shader needs alpha blending, combine the color and
        # alpha with a Transparent BSDF + Mix Shader.
        if self.use_alpha:
            node_mix = self.nodes.new('ShaderNodeMixShader')
            node_trans = self.nodes.new('ShaderNodeBsdfTransparent')

            node_mix.location = 1950, 200
            node_trans.location = 1680, 400

            self.connect('Combined Alpha', node_mix.inputs[0])
            self.connect(node_trans.outputs[0], node_mix.inputs[1])
            self.connect('Combined Color', node_mix.inputs[2])

        node_out = self.nodes.new('ShaderNodeOutputMaterial')
        node_out.location = 2250, 160
        if self.use_alpha:
            self.connect(node_mix.outputs[0], node_out.inputs[0])
        else:
            self.connect('Combined Color', node_out.inputs[0])

    def make_color_combiner(self, combiner, location):
        a, b, c, d = combiner
        x, y = location

        # A - B
        node, in1, in2, out1 = self.new_color_math_node('SUBTRACT')
        node.location = x, y
        self.connect(a, in1)
        self.connect(b, in2)

        # * C
        node, in1, in2, out2 = self.new_color_math_node('MULTIPLY')
        node.location = x + 230, y - 120
        self.connect(out1, in1)
        self.connect(c, in2)

        # + D
        node, in1, in2, out3 = self.new_color_math_node('ADD')
        node.location = x + 460, y - 240
        self.connect(out2, in1)
        self.connect(d, in2)

        self.vars['Combined Color'] = out3

    def make_alpha_combiner(self, combiner, location):
        a, b, c, d = combiner
        x, y = location

        # A - B
        node1 = self.new_node('ShaderNodeMath')
        node1.operation = 'SUBTRACT'
        node1.location = x + 90, y - 130
        self.connect(a, node1.inputs[0])
        self.connect(b, node1.inputs[1])

        # * C + D
        node2 = self.new_node('ShaderNodeMath')
        node2.operation = 'MULTIPLY_ADD'
        node2.location = x + 370, y - 150
        self.connect(node1.outputs[0], node2.inputs[0])
        self.connect(c, node2.inputs[1])
        self.connect(d, node2.inputs[2])

        self.vars['Combined Alpha'] = node2.outputs[0]

    def make_simple_blender_mix_node(self, blender):
        # Creates a node for the blender in the simple case when it can be
        # implemented by a MixRGB (lerp) node. Returns the node if created,
        # or None if the blender wasn't simple enough.

        p, a, m, b = blender

        # It's simple if...
        is_simple = (
            # b = 1 - a, so that
            # (p*a + m*(1-a)) / (a + (1-a)) = mix(m, p, a)
            b == 'One Minus A' and
            # Reading from framebuffer is not required
            p not in ['Framebuffer Color', 'Framebuffer Alpha'] and
            m not in ['Framebuffer Color', 'Framebuffer Alpha']
        )
        if not is_simple:
            return None

        node, in1, in2, out = self.new_color_math_node('MIX')
        self.connect(a, node.inputs[0])
        self.connect(m, in1)
        self.connect(p, in2)
        self.vars['Combined Color'] = out

        return node

    def make_inputs(self, tex0, tex1, input_vars):
        x, y = -1100, 500

        for var in input_vars:
            # Already created?
            if var in self.vars:
                continue

            # Texture inputs
            for i in range(2):
                if var in [f'Texel {i} Color', f'Texel {i} Alpha']:
                    tex = tex0 if i == 0 else tex1
                    node = self.make_texture_unit(tex, i, location=(x, y))
                    y -= 400
                    self.vars[f'Texel {i} Color'] = node.outputs['Color']
                    self.vars[f'Texel {i} Alpha'] = node.outputs['Alpha']

            # Vertex Color inputs
            for vc in ['Shade', 'Primitive', 'Env', 'Blend', 'Fog']:
                if var in [f'{vc} Color', f'{vc} Alpha']:
                    node = self.new_node('ShaderNodeVertexColor')
                    node.location = x, y
                    y -= 200
                    node.layer_name = f'{vc} Color'
                    node.name = node.label = f'{vc} Color'
                    self.vars[f'{vc} Color'] = node.outputs['Color']
                    self.vars[f'{vc} Alpha'] = node.outputs['Alpha']

            # Fog Level
            if var == 'Fog Level':
                node = self.new_node('ShaderNodeAttribute')
                node.location = x, y
                y -= 290
                node.attribute_name = 'Fog Level'
                node.name = node.label = 'Fog Level'
                self.vars['Fog Level'] = node.outputs['Fac']

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
            for un_var in unimplemented:
                if var == un_var:
                    print('GLR Import: unimplemented color combiner input:', un_var)
                    node = self.new_node('ShaderNodeRGB')
                    node.location = x, y
                    y -= 300
                    node.outputs[0].default_value = (0.0, 1.0, 1.0, 1.0)
                    node.label = f'{un_var} (UNIMPLEMENTED)'
                    self.vars[un_var] = node.outputs[0]

    def load_image(self, crc):
        if crc == 0:
            return None

        filepath = os.path.join(self.texture_dir, f'{crc:016X}.png')
        try:
            image = bpy.data.images.load(filepath, check_existing=True)
        except Exception:
            # Image didn't exist
            # Allow the path to be resolved later
            image = bpy.data.images.new(os.path.basename(filepath), 16, 16)
            image.filepath = filepath
            image.source = 'FILE'

        return image

    def make_texture_unit(self, tex, tex_num, location):
        nodes = self.nodes
        links = self.links
        x, y = location

        # Image Texture node
        node_tex = nodes.new('ShaderNodeTexImage')
        node_tex.name = node_tex.label = f'Texture {tex_num}'
        node_tex.width = 290
        node_tex.location = x - 150, y
        node_tex.image = self.load_image(tex['crc'])
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
        node_uv.name = node_uv.label = f'UV Map Texture {tex_num}'
        node_uv.location = x - 160, y - 70
        node_uv.uv_map = tex['uv_map']
        links.new(uv_socket, node_uv.outputs[0])

        return node_tex


def show_texture_info(tex):
    crc = tex['crc']
    tfilter = tex['filter']
    wrapS = tex['wrapS']
    wrapT = tex['wrapT']

    wrap = wrapS if wrapS == wrapT else f'{wrapS} x {wrapT}'

    return f'{crc:016X}, {tfilter}, {wrap}'
