"""ROS image unpacking shared by local perception and JPEG transport."""
import cv2
import numpy as np


def stamp(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec/1e9


def image_array(msg):
    types = {'rgb8':(np.uint8,3),'bgr8':(np.uint8,3),'16UC1':(np.uint16,1),'32FC1':(np.float32,1)}
    dtype, channels = types[msg.encoding]
    dtype = np.dtype(dtype).newbyteorder('>' if msg.is_bigendian else '<')
    data = np.ndarray((msg.height,msg.width,channels), dtype=dtype,
                      buffer=bytes(msg.data), strides=(msg.step,dtype.itemsize*channels,dtype.itemsize)).copy()
    if channels == 1:
        return data[:,:,0].astype(np.float32) * (.001 if msg.encoding == '16UC1' else 1.)
    return cv2.cvtColor(data,cv2.COLOR_RGB2BGR) if msg.encoding == 'rgb8' else data

