#!/usr/bin/env python3
import socket
import sys
import time
import numpy as np
import PIL
from PIL import Image
from PIL import ImageSequence

UDP_IP = '192.168.10.30'
UDP_PORT = 0x80FF

num_rows = 128
num_cols = num_rows
fbuf = np.zeros((64*4), dtype='u4')

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

im = Image.open(sys.argv[1])
size = 128, 128

frame_time = float(sys.argv[2])

while(1):
    for frame in ImageSequence.Iterator(im):
        thumb = PIL.ImageOps.pad(frame, size, Image.Resampling.LANCZOS)
        thumb = thumb.convert("RGB")
        cast = np.array(thumb)
        for i in range(4):
            for y in range(64):
                for x in range(64):
                    addr = ((y & 0x3F) << 6) | (x & 0x3F);
        
                    adj_y = y
                    adj_x = x
                    if i == 1:
                        adj_x = adj_x + 64
                    if i == 2:
                        adj_y = adj_y + 64
                    if i == 3:
                        adj_x = adj_x + 64
                        adj_y = adj_y + 64
                    r = cast[adj_y][adj_x][0].item()
                    g = cast[adj_y][adj_x][1].item()
                    b = cast[adj_y][adj_x][2].item()
        
                    fbuf[x+(64*(y%4))] = socket.htonl((addr << 18) | (((int(r))&0xFC) << 10)
                                                                       | (((int(g))&0xFC) << 4)
                                                                       | (((int(b))&0xFC) >> 2))
                if (y % 4) == 3:
                    tosend = bytearray()
                    tosend.append(1 << i)
                    tosend.append(0)
                    tosend.append(0)
                    tosend.append(0)
                    tosend.extend(fbuf.tobytes())
                    s.sendto(tosend, (UDP_IP, UDP_PORT))
 
        time.sleep(frame_time)

exit()
