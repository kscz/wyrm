#!/usr/bin/env python3
import socket
import sys
import time
import numpy as np
import cv2

UDP_IP = '192.168.10.30'
UDP_PORT = 1234

# We send 4 lines at a time, and we only access one 64x64 segment at a time
fbuf = np.zeros((64*4), dtype='u4')

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Open the stream using OpenCV
vidcap = cv2.VideoCapture(sys.argv[1])

fps = vidcap.get(cv2.CAP_PROP_FPS)
frame_time = 1.0/float(fps)

# Read in the first frame of the video to calculate all our parameters
success, im = vidcap.read()

# First we need to calculate how to resize while maintaining the original aspect ratio
o_shape = (im.shape[1], im.shape[0])
n_shape = (128, 128)
ratio = float(max(n_shape))/float(max(o_shape))
n_size = tuple([int(x*ratio) for x in o_shape])

# Now we need to calculate the border we add after resizing to hit a square 128x128 size
delta_w = n_shape[0] - n_size[0]
delta_h = n_shape[1] - n_size[1]
top, bottom = delta_h//2, delta_h-(delta_h//2)
left, right = delta_w//2, delta_w-(delta_w//2)

# While we have frame data - send new frames to the display!
while success:
    start_time = time.monotonic()
    im = cv2.resize(im, n_size)
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=((0, 0, 0)))
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    cast = np.array(im)

    # The loop is broken into 64x64 segments - 4 for a 128x128 frame
    for i in range(4):
        for y in range(64):
            for x in range(64):
                # Addresses are local to each segment
                addr = ((y & 0x3F) << 6) | (x & 0x3F);

                # Calculate our pointer into the larger 128x128 frame
                adj_y = y
                adj_x = x
                if i == 1:
                    adj_x = adj_x + 64
                if i == 2:
                    adj_y = adj_y + 64
                if i == 3:
                    adj_x = adj_x + 64
                    adj_y = adj_y + 64
                r = cast[adj_y][adj_x][2].item()
                g = cast[adj_y][adj_x][0].item()
                b = cast[adj_y][adj_x][1].item()

                # We buffer up 4 lines per packet
                fbuf[x+(64*(y%4))] = socket.htonl((addr << 18) | (((int(r))&0xFC) << 10)
                                                               | (((int(g))&0xFC) << 4)
                                                               | (((int(b))&0xFC) >> 2))
            # If we have 4 lines in the buffer, send!
            if (y % 4) == 3:
                tosend = bytearray()
                tosend.append(1 << i) # The panel we're sending to is encoded here
                tosend.append(0)
                tosend.extend(fbuf.tobytes())
                s.sendto(tosend, (UDP_IP, UDP_PORT))

    # Calculate how long we took to send a complete frame
    end_time = time.monotonic()
    proc_time = end_time - start_time
    # If we were faster than the FPS, pause until we're at the frame limit
    if frame_time > proc_time:
        time.sleep(frame_time - proc_time)

    # Pull in the next frame!
    success, im = vidcap.read()

exit()
