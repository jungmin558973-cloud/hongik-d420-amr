"""D420 CODBOT 4WD serial helpers, adapted from the previously working scripts.
No hardware access and no ROS dependency at import time.
"""
import math
import time

BAUD = 115200


def xor_checksum(bs):
    x = 0
    for b in bs:
        x ^= b
    return x


def motion_packet(vx=0.0, wz=0.0, *, max_linear_speed=0.30, max_angular_speed=0.50):
    """4WD 13-byte 115200 baud packet; default linear envelope is 0.30 m/s; D420 catalogue ceiling is 1.50 m/s.
    vx: forward m/s, wz: CCW rad/s; firmware protocol gains mm/s, 0.01 rad/s.
    """
    if not math.isfinite(vx) or not math.isfinite(wz):
        raise ValueError('Velocity is not finite')
    if isinstance(max_linear_speed, bool) or not math.isfinite(max_linear_speed) or not 0.03 <= max_linear_speed <= 1.5:
        raise ValueError('Invalid D420 speed envelope')
    if isinstance(max_angular_speed, bool) or not math.isfinite(max_angular_speed) or not 0.05 <= max_angular_speed <= 5.4:
        raise ValueError('Invalid configured angular envelope')
    if abs(vx) > max_linear_speed + 0.000001 or abs(wz) > max_angular_speed + 0.000001:
        raise ValueError('Exceeds configured command envelope')
    x, w = round(abs(vx) * 1000), round(abs(wz) * 100)
    fields = bytes([1, int(vx < 0), x >> 8, x & 255, 0, 0, 0,
                    int(wz < 0), w >> 8, w & 255])
    return b'\xCD\x0A' + fields + bytes([xor_checksum(fields)])


STOP = motion_packet()
assert STOP.hex(' ') == 'cd 0a 01 00 00 00 00 00 00 00 00 00 01'
assert motion_packet(.1, 0).hex(' ') == 'cd 0a 01 00 00 64 00 00 00 00 00 00 65'


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.good = 0
        self.bad = 0
        self.is_4wd = False
        self.v = None
        self.last_frame = 0.0
        self.last_velocity = 0.0
        self.last_yaw = 0.0
        self.yaw = None
        self.initial_yaw = None

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        self.buffer.extend(data)
        if len(self.buffer) > 32768:
            self.buffer = self.buffer[-256:]
        while True:
            p = self.buffer.find(b'\xfd')
            if p < 0:
                self.buffer.clear()
                break
            if p:
                del self.buffer[:p]
            if len(self.buffer) < 3:
                break
            length = self.buffer[1]
            if not (2 <= length <= 31):
                del self.buffer[0]
                continue
            size = length + 3
            if len(self.buffer) < size:
                break
            pkt = self.buffer[:size]
            if xor_checksum(pkt[2:-1]) != pkt[-1]:
                self.bad += 1
                del self.buffer[0]
                continue
            self.good += 1
            self.last_frame = now
            function = pkt[2]
            if function == 7 and length == 2 and pkt[3] == 2:
                self.is_4wd = True
            elif function == 2 and length == 10:
                def signed(sign, idx):
                    value = pkt[idx] * 256 + pkt[idx + 1]
                    return -value if pkt[sign] else value
                self.v = (signed(3, 4)/1000., signed(6, 7)/1000., signed(9, 10)/100.)
                self.last_velocity = now
            elif function == 5 and length == 10:
                raw_degree = (-1 if pkt[9] else 1) * (pkt[10] * 256 + pkt[11]) / 100.0
                abs_yaw = math.radians(raw_degree)
                if self.initial_yaw is None:
                    self.initial_yaw = abs_yaw
                self.yaw = (-(abs_yaw - self.initial_yaw) + math.pi) % (2 * math.pi) - math.pi
                self.last_yaw = now
            del self.buffer[:size]


def test():
    def rx_frame(*fields):
        return bytes([0xfd, len(fields), *fields, xor_checksum(fields)])
    d = Decoder()
    car = rx_frame(7, 2)
    vel = rx_frame(2, 0, 0, 100, 0, 0, 0, 0, 0, 0)
    yaw = rx_frame(5, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    combined = car + vel + yaw
    for section in [combined[:2], combined[2:9], combined[9:]]:
        d.feed(section)
    assert d.is_4wd and d.v == (0.1, 0., 0.) and d.yaw == 0., (d.v, d.yaw)
    assert d.good == 3 and d.bad == 0
    assert len(STOP) == 13 and len(motion_packet(.1, .3)) == 13
    assert motion_packet(.3, 0).hex(' ') == 'cd 0a 01 00 01 2c 00 00 00 00 00 00 2c'
    for bad in ((float('nan'), 0), (.4, 0), (0, 1)):
        try:
            motion_packet(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'unsafe packet accepted {bad}')
    print('SELFTEST PASS — frame decoder, ID, velocity, IMU yaw, command checksum, limits')


if __name__ == '__main__':
    test()
