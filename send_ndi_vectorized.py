#!/usr/bin/env python3
import argparse
import socket
import sys
import time
import numpy as np
import cv2
import NDIlib as ndi

parser = argparse.ArgumentParser(
        prog='send_ndi_vid',
        description='Send an NDI stream to some wyrm eyes')

parser.add_argument('stream')
parser.add_argument("--ip", action="extend", nargs="+", type=str)

args = parser.parse_args()

UDP_PORT = 0x80ff

# We send 4 lines at a time, and we only access one 64x64 segment at a time
fbuf = np.zeros((64*4), dtype='>u4')  # Use big-endian dtype
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

if not ndi.initialize():
    exit(1)

ndi_find = ndi.find_create_v2()

if ndi_find is None:
    exit(2)

found_source = False
sources = []
while not found_source:
    print('Looking for sources ...')
    ndi.find_wait_for_sources(ndi_find, 2000)
    sources = ndi.find_get_current_sources(ndi_find)
    for source in sources:
        if args.stream in source.ndi_name:
            found_source = True
            break

ndi_recv_create = ndi.RecvCreateV3()
ndi_recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA

ndi_recv = ndi.recv_create_v3(ndi_recv_create)

if ndi_recv is None:
    exit(3)

found = False
for source in sources:
    print(source.ndi_name)
    if args.stream in source.ndi_name:
        ndi.recv_connect(ndi_recv, source)
        found = True
        break

ndi.find_destroy(ndi_find)

if not found:
    print("Found no source!")
    exit(1)

# Read in the first frame of the video to calculate all our parameters
t, v, _, _ = ndi.recv_capture_v2(ndi_recv, 5000)

while t != ndi.FRAME_TYPE_VIDEO:
    t, v, _, _ = ndi.recv_capture_v2(ndi_recv, 5000)

print('Video data received (%dx%d).' % (v.xres, v.yres))
im = np.copy(v.data)
ndi.recv_free_video_v2(ndi_recv, v)

# Calculate resize parameters while maintaining aspect ratio
o_shape = (v.xres, v.yres)
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

while True:
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
            for ip in args.ip:
                s.sendto(tosend, (ip, UDP_PORT))
        
    # Frame timing management
    end_time = time.monotonic()
    proc_time = end_time - start_time
    
    # Get next frame
    t, v, _, _ = ndi.recv_capture_v2(ndi_recv, 5000)

    while t != ndi.FRAME_TYPE_VIDEO:
        t, v, _, _ = ndi.recv_capture_v2(ndi_recv, 5000)

    im = np.copy(v.data)
    ndi.recv_free_video_v2(ndi_recv, v)

exit()
