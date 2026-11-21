"""Image/grid encoding outside the ROS executor and motion-control loop."""
import io
import math
import numpy as np
from PIL import Image


def encode_camera(width, height, encoding, step, data):
    formats = {'rgb8': ('RGB', 3), 'bgr8': ('BGR', 3), 'rgba8': ('RGBA', 4),
               'bgra8': ('BGRA', 4), 'mono8': ('L', 1)}
    if encoding not in formats:
        raise ValueError(f'Unsupported camera encoding: {encoding}')
    raw, channels = formats[encoding]
    if not (0 < width <= 4096 and 0 < height <= 2160) or step < width*channels or len(data) < step*height:
        raise ValueError('Invalid camera frame dimensions/stride')
    mode = 'L' if channels == 1 else 'RGBA' if channels == 4 else 'RGB'
    frame = Image.frombytes(mode, (width, height), data, 'raw', raw, step, 1).convert('RGB')
    frame.thumbnail((640,480))
    output = io.BytesIO()
    frame.save(output, format='JPEG', quality=72)
    return output.getvalue()


def encode_grid(width, height, data):
    if not 0 < width*height <= 4_000_000 or len(data) != width*height:
        raise ValueError('Invalid occupancy grid dimensions')
    raw = np.asarray(data, dtype=np.int16).reshape(height,width)
    stride = max(1, math.ceil(max(width,height)/640))
    # Conservative max pooling: never drop a thin occupied cell during decimation.
    rows, cols = math.ceil(height/stride), math.ceil(width/stride)
    padded = np.full((rows*stride,cols*stride),-1,dtype=np.int16)
    padded[:height,:width] = raw
    reduced = padded.reshape(rows,stride,cols,stride).max(axis=(1,3))
    rgba = np.zeros((rows,cols,4),dtype=np.uint8)
    rgba[reduced >= 0] = (202,218,202,35)
    occupied = reduced >= 50
    rgba[occupied] = (40,64,55,210)
    output = io.BytesIO()
    Image.fromarray(np.flipud(rgba)).save(output, format='PNG')
    return output.getvalue(), cols, rows, stride
