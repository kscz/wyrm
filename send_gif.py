#!/bin/python3
import socket
import sys
import time
import numpy as np
import PIL
from PIL import Image
from PIL import ImageSequence

UDP_IP = '192.168.10.30'
UDP_PORT = 11223

num_rows = 64
num_cols = num_rows
fbuf = np.zeros((num_rows), dtype='u4')

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

im = Image.open(sys.argv[1])
size = 64, 64

frame_time = float(sys.argv[2])

while(1):
    for frame in ImageSequence.Iterator(im):
        thumb = PIL.ImageOps.pad(frame, size, Image.Resampling.LANCZOS)
        thumb = thumb.convert("RGB")
        cast = np.array(thumb)
        for y in range(num_rows):
            for x in range(num_cols):
                addr = ((y & 0x3F) << 6) | (x & 0x3F);
        
                r = cast[y][x][2].item()
                g = cast[y][x][0].item()
                b = cast[y][x][1].item()
        
                fbuf[x] = socket.htonl((addr << 18)     | (((int(r))&0xFC) << 10)
                                                        | (((int(g))&0xFC) << 4)
                                                        | (((int(b))&0xFC) >> 2))
        
            s.sendto(fbuf.tobytes(), (UDP_IP, UDP_PORT))
        time.sleep(frame_time)

exit()
