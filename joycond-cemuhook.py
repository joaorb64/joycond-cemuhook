import sys
import evdev
from threading import Thread
import socket
import struct
from binascii import crc32
from time import time

class Message(list):
    Types = dict(version=bytes([0x00, 0x00, 0x10, 0x00]),
                 ports=bytes([0x01, 0x00, 0x10, 0x00]),
                 data=bytes([0x02, 0x00, 0x10, 0x00]))

    def __init__(self, message_type, data):
        self.extend([
            0x44, 0x53, 0x55, 0x53,  # DSUS,
            0xE9, 0x03,  # protocol version (1001),
        ])

        # data length
        self.extend((len(data) + 4).to_bytes(2, byteorder='little'))

        self.extend([
            0x00, 0x00, 0x00, 0x00,  # place for CRC32
            0xff, 0xff, 0xff, 0xff,  # server ID
        ])

        self.extend(Message.Types[message_type])  # data type

        self.extend(data)

        # CRC32
        self[8:12] = crc32(bytes(self)).to_bytes(4, byteorder='little')


class UDPServer:
    def __init__(self, host='', port=26760, device=None, wine_mode=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.counter = 0
        self.client = None
        self.device = device
        self.wine_mode = wine_mode

        self.motion_x = 0
        self.motion_y = 0
        self.motion_z = 0
        
        self.accel_x = 0
        self.accel_y = 0
        self.accel_z = 0

    def _res_ports(self, index):
        return Message('ports', [
            index,  # pad id
            0x02,  # state (connected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0xff,  # MAC 00:00:00:00:00:FF
            0xef,  # battery (charged)
            0x00,  # ?
        ])

    def _req_ports(self, message, address):
        requests_count = int.from_bytes(message[20:24], byteorder='little')
        for i in range(requests_count):
            index = message[24 + i]
            if (index != 0):  # we have only one controller
                continue
            self.sock.sendto(bytes(self._res_ports(index)), address)

    def _req_data(self, message, address):
        flags = message[24]
        reg_id = message[25]
        # reg_mac = message[26:32]

        if flags == 0 and reg_id == 0:  # TODO: Check MAC
            self.client = address
            self.last_request = time()

    def _handle_request(self, request):
        message, address = request

        # client_id = message[12:16]
        msg_type = message[16:20]

        if msg_type == Message.Types['version']:
            return
        elif msg_type == Message.Types['ports']:
            self._req_ports(message, address)
        elif msg_type == Message.Types['data']:
            self._req_data(message, address)
        else:
            print('Unknown message type: ' + str(msg_type))

    def send(self):
        if not self.client:
            return None

        data = [
            0x00,  # pad id
            0x02,  # state (connected)
            0x02,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0xff,  # MAC 00:00:00:00:00:FF
            0xef,  # battery (charged)
            0x01  # is active (true)
        ]

        data.extend(struct.pack('<I', self.counter))
        self.counter += 1

        data.extend([
            0x00,  # left, down, right, up, options, R3, L3, share
            0x00,  # square, cross, circle, triangle, r1, l1, r2, l2
            0x00,  # PS
            0x00,  # Touch

            0x00,  # position left x
            0x00,  # position left y
            0x00,  # position right x
            0x00,  # position right y

            0x00,  # dpad left
            0x00,  # dpad down
            0x00,  # dpad right
            0x00,  # dpad up

            0x00,  # square
            0x00,  # cross
            0x00,  # circle
            0x00,  # triange

            0x00,  # r1
            0x00,  # l1

            0x00,  # r2
            0x00,  # l2

            0x00,  # track pad first is active (false)
            0x00,  # track pad first id

            0x00, 0x00,  # trackpad first x
            0x00, 0x00,  # trackpad first y

            0x00,  # track pad second is active (false)
            0x00,  # track pad second id

            0x00, 0x00,  # trackpad second x
            0x00, 0x00,  # trackpad second y
        ])

        data.extend(struct.pack('<d', time() * 10**6))

        if self.wine_mode == True:
            self.motion_x = self.motion_x/4
            self.motion_y = self.motion_y/4
            self.motion_z = self.motion_z/4

        if self.device and self.device.name == "Nintendo Switch Pro Controller IMU":
            sensors = [
                self.accel_y / 4000,
                - self.accel_z / 4000,
                self.accel_x / 4000,
                - self.motion_y / (10),
                - self.motion_z / (10),
                self.motion_x / (10),
            ]
        else:
            sensors = [
                - self.accel_y / 4000,
                self.accel_z / 4000,
                self.accel_x / 4000,
                self.motion_y / (10),
                self.motion_z / (10),
                self.motion_x / (10),
            ]

        self.motion_x = 0
        self.motion_y = 0
        self.motion_z = 0

        for sensor in sensors:
            data.extend(struct.pack('<f', float(sensor)))

        self.sock.sendto(bytes(Message('data', data)), self.client)

    def report(self, device):
        self.send()

    def _worker(self):
        while True:
            self._handle_request(self.sock.recvfrom(1024))

    def start(self):
        self.thread = Thread(target=self._worker)
        self.thread.daemon = True
        self.thread.start()

device = None
devices = [evdev.InputDevice(path) for path in evdev.list_devices()]

for d in devices:
    if d.name == "Nintendo Switch Right Joy-Con IMU":
        device = d
        break
    if d.name == "Nintendo Switch Pro Controller IMU":
        device = d
        break

if device == None:
    print("No \"Nintendo Switch Right Joy-Con IMU\" or \"Nintendo Switch Pro Controller IMU\" detected")
    quit()

print("Controller detected: " + device.name)

wine_mode = False
if len(sys.argv) > 1 and sys.argv[1] == "wine":
    wine_mode = True
    print("Starting in Wine mode")

server = UDPServer('127.0.0.1', 26760, device, wine_mode)
server.start()

for event in device.read_loop():
    if event.type == evdev.ecodes.EV_MSC:
        server.report(device)
    if event.type == evdev.ecodes.EV_ABS:
        if event.code == evdev.ecodes.ABS_RX:
            if abs(event.value) > 100:
                server.motion_x += event.value
        if event.code == evdev.ecodes.ABS_RY:
            if abs(event.value) > 100:
                server.motion_y += event.value
        if event.code == evdev.ecodes.ABS_RZ:
            if abs(event.value) > 100:
                server.motion_z += event.value
        if event.code == evdev.ecodes.ABS_X:
            server.accel_x = event.value
        if event.code == evdev.ecodes.ABS_Y:
            server.accel_y = event.value
        if event.code == evdev.ecodes.ABS_Z:
            server.accel_z = event.value