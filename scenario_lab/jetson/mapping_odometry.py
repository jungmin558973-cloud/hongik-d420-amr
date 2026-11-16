"""Correct the verified forward encoder sign for mapping, preserving other axes."""


def forward_decoder(base):
    class ForwardDecoder(base):
        def feed(self,data,now=None):
            # Restore the base decoder's convention before feeding partial/IMU
            # packets, then convert its final value exactly once. This also
            # handles multiple velocity feeds carrying an identical timestamp.
            if self.v is not None:self.v=(-self.v[0],self.v[1],self.v[2])
            try:super().feed(data,now)
            finally:
                if self.v is not None:self.v=(-self.v[0],self.v[1],self.v[2])
    return ForwardDecoder
