#!/usr/bin/env python3
import socket
import sys
import time
import numpy as np
import cv2

UDP_IP = '192.168.10.30'
UDP_PORT = 0x80ff

# Detect endianness and create vectorized htonl
def is_big_endian():
    return np.array([0x01020304], dtype='>u4').view(np.uint8)[0] == 0x01

NEED_SWAP = not is_big_endian()
def htonl_vec(arr):
    if NEED_SWAP:
        return arr.byteswap()
    return arr

# We send 4 lines at a time, and we only access one 64x64 segment at a time
fbuf = np.zeros((64*4), dtype='>u4')  # Use big-endian dtype
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Open the stream using OpenCV
vidcap = cv2.VideoCapture(sys.argv[1])
fps = vidcap.get(cv2.CAP_PROP_FPS)
frame_time = 1.0/float(fps)

# Read in the first frame of the video to calculate all our parameters
success, im = vidcap.read()

# Calculate resize parameters while maintaining aspect ratio
o_shape = (im.shape[1], im.shape[0])
n_shape = (128, 128)
ratio = float(max(n_shape))/float(max(o_shape))
n_size = tuple([int(x*ratio) for x in o_shape])

# Calculate border parameters for 128x128 square
delta_w = n_shape[0] - n_size[0]
delta_h = n_shape[1] - n_size[1]
top, bottom = delta_h//2, delta_h-(delta_h//2)
left, right = delta_w//2, delta_w-(delta_w//2)

# Pre-calculate address matrices for each segment
y_indices, x_indices = np.meshgrid(np.arange(64), np.arange(64), indexing='ij')
base_addr = ((y_indices & 0x3F) << 6) | (x_indices & 0x3F)

# Define segment offsets
segment_offsets = [
    (0, 0),    # Segment 0
    (0, 64),   # Segment 1
    (64, 0),   # Segment 2
    (64, 64)   # Segment 3
]

while success:
    start_time = time.monotonic()
    
    # Process frame
    im = cv2.resize(im, n_size)
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=((0, 0, 0)))
    
    # Convert to numpy array and ensure correct data type
    frame = np.array(im, dtype=np.uint8)
    
    # Process each 64x64 segment
    for i, (y_offset, x_offset) in enumerate(segment_offsets):
        # Extract segment
        segment = frame[y_offset:y_offset+64, x_offset:x_offset+64]
        
        # Extract RGB components as uint32 so we can shift them up into position
        r = segment[:, :, 0].astype(np.uint32)
        g = segment[:, :, 1].astype(np.uint32)
        b = segment[:, :, 2].astype(np.uint32)
        
        # Vectorized pixel packing
        packed_pixels = np.array(
            (base_addr << 18) |
            ((b & 0xFC) << 10) |
            ((g & 0xFC) << 4) |
            ((r & 0xFC) >> 2)
        )
        
        # Process in groups of 4 lines
        for y in range(0, 64, 4):
            # Fill buffer with 4 lines of packed pixels
            fbuf[0:256] = packed_pixels[y:y+4].flatten()
            
            # Prepare and send packet
            tosend = bytearray()
            tosend.append(1 << i)  # Panel indicator
            tosend.append(0)
            tosend.append(0)
            tosend.append(0)
            tosend.extend(fbuf)  # Already in network byte order
            s.sendto(tosend, (UDP_IP, UDP_PORT))
            #time.sleep(0.0005) # The FPGA can't keep up currently - give it a pause
    
    # Frame timing management
    end_time = time.monotonic()
    proc_time = end_time - start_time
    if frame_time > proc_time:
        time.sleep(frame_time - proc_time)
    
    # Get next frame
    success, im = vidcap.read()

exit()
