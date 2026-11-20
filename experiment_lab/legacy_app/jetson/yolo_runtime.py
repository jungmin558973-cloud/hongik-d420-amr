"""Static YOLO11 ONNX output on TensorRT 10; no PyTorch needed on Jetson."""
import ctypes as C
import ctypes.util
import cv2
import numpy as np


class Yolo:
    def __init__(self, path):
        import tensorrt as trt
        self.trt = trt
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
        with open(path, 'rb') as file:
            self.engine = self.runtime.deserialize_cuda_engine(file.read())
        if self.engine is None:
            raise RuntimeError('TensorRT 엔진을 읽지 못했습니다. Jetson에서 다시 생성하세요.')
        self.context = self.engine.create_execution_context()
        self.cuda = C.CDLL(ctypes.util.find_library('cudart') or 'libcudart.so.12')
        self.cuda.cudaMalloc.argtypes = [C.POINTER(C.c_void_p), C.c_size_t]
        self.cuda.cudaMemcpy.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.c_int]
        self.cuda.cudaFree.argtypes = [C.c_void_p]
        self.cuda.cudaStreamCreate.argtypes = [C.POINTER(C.c_void_p)]
        self.cuda.cudaStreamSynchronize.argtypes = [C.c_void_p]
        self.cuda.cudaStreamDestroy.argtypes = [C.c_void_p]
        self.stream = C.c_void_p()
        self.check(self.cuda.cudaStreamCreate(C.byref(self.stream)))
        self.buffers = {}
        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            shape = tuple(self.engine.get_tensor_shape(name))
            if any(d <= 0 for d in shape):
                raise ValueError('고정 크기 엔진이 필요합니다.')
            host = np.empty(shape, dtype=trt.nptype(self.engine.get_tensor_dtype(name)))
            device = C.c_void_p()
            self.check(self.cuda.cudaMalloc(C.byref(device), host.nbytes))
            self.context.set_tensor_address(name, device.value)
            self.buffers[name] = (host, device)
            if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                self.input = name
            else:
                self.output = name
        if len(self.buffers) != 2:
            raise ValueError('YOLO detect 입출력만 지원합니다.')
        self.size = self.buffers[self.input][0].shape[-1]

    @staticmethod
    def check(code):
        if code:
            raise RuntimeError(f'CUDA error {code}')

    def detect(self, bgr):
        h, w = bgr.shape[:2]
        scale = self.size / max(h, w)
        rw, rh = round(w*scale), round(h*scale)
        left, top = (self.size-rw)//2, (self.size-rh)//2
        image = np.full((self.size, self.size, 3), 114, np.uint8)
        image[top:top+rh, left:left+rw] = cv2.resize(bgr, (rw, rh))
        host, device = self.buffers[self.input]
        host[:] = image[:, :, ::-1].transpose(2, 0, 1)[None] / 255.
        self.check(self.cuda.cudaMemcpy(device, C.c_void_p(host.ctypes.data), host.nbytes, 1))
        if not self.context.execute_async_v3(self.stream.value):
            raise RuntimeError('TensorRT 실행 실패')
        self.check(self.cuda.cudaStreamSynchronize(self.stream))
        out, out_device = self.buffers[self.output]
        self.check(self.cuda.cudaMemcpy(C.c_void_p(out.ctypes.data), out_device, out.nbytes, 2))
        pred = out[0].T
        # COCO person=0; use the winning class, not an isolated person score.
        scores = pred[:, 4:]
        rows = pred[(scores.argmax(axis=1) == 0) & (scores[:, 0] >= .55)]
        boxes, confidences = [], []
        for row in rows:
            x, y, bw, bh = row[:4]
            x1, y1 = max(0., (x-bw/2-left)/scale), max(0., (y-bh/2-top)/scale)
            x2, y2 = min(float(w), (x+bw/2-left)/scale), min(float(h), (y+bh/2-top)/scale)
            if x2-x1 < 15 or y2-y1 < 40:
                continue
            boxes.append([x1, y1, x2-x1, y2-y1]); confidences.append(float(row[4]))
        indices = cv2.dnn.NMSBoxes(boxes, confidences, .55, .45)
        return [{'box': boxes[int(i)], 'confidence': confidences[int(i)]}
                for i in np.asarray(indices).reshape(-1)]

    def close(self):
        for _, device in self.buffers.values():
            self.cuda.cudaFree(device)
        self.buffers.clear()
        self.cuda.cudaStreamDestroy(self.stream)
