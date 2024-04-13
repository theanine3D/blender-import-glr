###############################
# Get N64 configuration state
###############################

def get_texture_filter(other_mode):
    # 0 = TF_POINT    Point Sampling
    # 1 = Invalid
    # 2 = TF_AVERAGE  Box Filtering
    # 3 = TF_BILERP   Bilinear (approximated with 3 samples)
    filter = (other_mode >> 44) & 0x3
    return 'Closest' if filter == 0 else 'Linear'


def get_texture_wrap_mode(wrap):
    # bit 0 = MIRROR
    # bit 1 = CLAMP
    if wrap == 0:   return 'Repeat'
    elif wrap == 1: return 'Mirror'
    else:           return 'Clamp'


def get_backface_culling(geometry_mode, microcode):
    # Determine backface culling
    # F3D/F3DEX: 0x2000 (0010 0000 0000 0000)
    # F3DEX2: 0x400 (0100 0000 0000)
    # TODO: Check others, assumed under F3D/F3DEX family
    mask = 0x2000
    if (
        microcode == 2 or  # F3DEX2
        microcode == 5 or  # L3DEX2
        microcode == 7 or  # S2DEX2
        microcode == 13 or # F3DEX2CBFD
        microcode == 17 or # F3DZEX2OOT
        microcode == 18 or # F3DZEX2MM
        microcode == 21    # F3DEX2ACCLAIM
    ):
        mask >>= 3

    return bool(geometry_mode & mask)


#########################
# Pretty-print formulas
#########################

def show_combiner_formula(a, b, c, d):
    # Formats (a-b)*c+d as a human readable string

    # sub = (a - b)
    if a == b:       sub = '0'
    elif b == '0':   sub = a
    elif a == '0':   sub = f'- {a}'
    else:            sub = f'({a} - {b})'

    # mul = sub * c
    if sub == '0':   mul = '0'
    elif c == '0':   mul = '0'
    elif sub == '1': mul = c
    elif c == '1':   mul = sub
    else:            mul = f'{sub} × {c}'

    # add = mul + d
    if mul == '0':   add = d
    elif d == '0':   add = mul
    else:            add = f'{mul} + {d}'

    return add


def show_blender_formula(p, a, m, b):
    # Formats (p*a + m*b)/(a+b) as a human readable string

    # pa = p * a
    if a == '0':     pa = '0'
    else:            pa = f'{p} × {a}'

    # mb = m * b
    if b == '0':     mb = '0'
    elif b == '1':   mb = m
    else:            mb = f'{m} × {b}'

    # num = (pa + mb)
    if pa == '0':    num = mb
    elif mb == '0':  num = pa
    else:            num = f'({pa} + {mb})'

    # den = (a + b)
    if a == '0':     den = b
    elif b == '0':   den = a
    elif b == 'One Minus A':  den = '1'
    elif (a,b) == ('0', '0'): den = '0'
    else:            den = f'({a} + {b})'

    # out = num / den
    if den == '1':   out = num
    elif num == '0': out = '0'
    elif num == den: out = '1'
    else:            out = f'{num} / {den}'

    return out


#######################
# Decode combiner mux
#######################

RGB_A_TABLE = {
    0: 'Combined Color',
    1: 'Texel 0 Color',
    2: 'Texel 1 Color',
    3: 'Primitive Color',
    4: 'Shade Color',
    5: 'Env Color',
    6: '1',
    7: 'Noise',
}

RGB_B_TABLE = {
    0: 'Combined Color',
    1: 'Texel 0 Color',
    2: 'Texel 1 Color',
    3: 'Primitive Color',
    4: 'Shade Color',
    5: 'Env Color',
    6: 'Key Center',
    7: 'Convert K4',
}

RGB_C_TABLE = {
    0: 'Combined Color',
    1: 'Texel 0 Color',
    2: 'Texel 1 Color',
    3: 'Primitive Color',
    4: 'Shade Color',
    5: 'Env Color',
    6: 'Key Scale',
    7: 'Combined Alpha',
    8: 'Texel 0 Alpha',
    9: 'Texel 1 Alpha',
    10: 'Primitive Alpha',
    11: 'Shade Alpha',
    12: 'Env Alpha',
    13: 'LOD Fraction',
    14: 'Primitive LOD Fraction',
    15: 'Convert K5',
}

RGB_D_TABLE = {
    0: 'Combined Color',
    1: 'Texel 0 Color',
    2: 'Texel 1 Color',
    3: 'Primitive Color',
    4: 'Shade Color',
    5: 'Env Color',
    6: '1',
    7: '0',
}

ALPHA_ABD_TABLE = {
    0: 'Combined Alpha',
    1: 'Texel 0 Alpha',
    2: 'Texel 1 Alpha',
    3: 'Primitive Alpha',
    4: 'Shade Alpha',
    5: 'Env Alpha',
    6: '1',
    7: '0',
}

ALPHA_C_TABLE = {
    0: 'LOD Fraction',
    1: 'Texel 0 Alpha',
    2: 'Texel 1 Alpha',
    3: 'Primitive Alpha',
    4: 'Shade Alpha',
    5: 'Env Alpha',
    6: 'Primitive LOD Fraction',
    7: '0',
}


def decode_combiner_mode(mux):
    # Decodes the u64 combiner mux value into the 16 input sources to
    # the color combiner.

    # {a,b,c,d}_** controls the a/b/c/d variable
    # *_{rgb,a}* controls the RGB/alpha equation
    # *_*{1,2} controls the 1st/2nd cycle
    a_rgb1 =  (mux >> 52) & 0xF
    c_rgb1 =  (mux >> 47) & 0x1F
    a_a1 =    (mux >> 44) & 0x7
    c_a1 =    (mux >> 41) & 0x7
    a_rgb2 =  (mux >> 37) & 0xF
    c_rgb2 =  (mux >> 32) & 0x1F
    b_rgb1 =  (mux >> 28) & 0xF
    b_rgb2 =  (mux >> 24) & 0xF
    a_a2 =    (mux >> 21) & 0x7
    c_a2 =    (mux >> 18) & 0x7
    d_rgb1 =  (mux >> 15) & 0x7
    b_a1 =    (mux >> 12) & 0x7
    d_a1 =    (mux >>  9) & 0x7
    d_rgb2 =  (mux >>  6) & 0x7
    b_a2 =    (mux >>  3) & 0x7
    d_a2 =    (mux >>  0) & 0x7

    # Convert numbers into readable strings
    rgb1 = decode_rgb_combiner_abcd(a_rgb1, b_rgb1, c_rgb1, d_rgb1)
    alpha1 = decode_alpha_combiner_abcd(a_a1, b_a1, c_a1, d_a1)
    rgb2 = decode_rgb_combiner_abcd(a_rgb2, b_rgb2, c_rgb2, d_rgb2)
    alpha2 = decode_alpha_combiner_abcd(a_a2, b_a2, c_a2, d_a2)

    return (*rgb1, *alpha1), (*rgb2, *alpha2)


def decode_rgb_combiner_abcd(a, b, c, d):
    # http://n64devkit.square7.ch/tutorial/graphics/4/image07.gif
    a = RGB_A_TABLE.get(a, '0')
    b = RGB_B_TABLE.get(b, '0')
    c = RGB_C_TABLE.get(c, '0')
    d = RGB_D_TABLE.get(d, '0')
    return a, b, c, d


def decode_alpha_combiner_abcd(a, b, c, d):
    # http://n64devkit.square7.ch/tutorial/graphics/5/image13.gif

    a = ALPHA_ABD_TABLE[a]
    b = ALPHA_ABD_TABLE[b]
    c = ALPHA_C_TABLE[c]
    d = ALPHA_ABD_TABLE[d]
    return a, b, c, d


######################
# Decode blender mux
######################

BLENDER_PM_TABLE = {
    0: 'Combined Color',
    1: 'Framebuffer Color',
    2: 'Blend Color',
    3: 'Fog Color',
}

BLENDER_A_TABLE = {
    0: 'Combined Alpha',
    1: 'Fog Alpha',
    2: 'Shade Alpha',
    3: '0',
}

BLENDER_B_TABLE = {
    0: 'One Minus A',
    1: 'Framebuffer Alpha',
    2: '1',
    3: '0',
}


def decode_blender_mode(other_mode):
    # Decodes the mux value in the other_mode state into the eight input
    # sources for the blender.

    # 1/2 means first/second cycle
    b_2 = (other_mode >> 16) & 0x3
    b_1 = (other_mode >> 18) & 0x3
    m_2 = (other_mode >> 20) & 0x3
    m_1 = (other_mode >> 22) & 0x3
    a_2 = (other_mode >> 24) & 0x3
    a_1 = (other_mode >> 26) & 0x3
    p_2 = (other_mode >> 28) & 0x3
    p_1 = (other_mode >> 30) & 0x3

    pamb1 = decode_blender_pamb(p_1, a_1, m_1, b_1)
    pamb2 = decode_blender_pamb(p_2, a_2, m_2, b_2)

    return pamb1, pamb2


def decode_blender_pamb(p, a, m, b):
    p = BLENDER_PM_TABLE[p]
    a = BLENDER_A_TABLE[a]
    m = BLENDER_PM_TABLE[m]
    b = BLENDER_B_TABLE[b]
    return p, a, m, b
