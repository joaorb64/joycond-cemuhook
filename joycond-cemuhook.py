#! /usr/bin/env python3

import signal
import sys
import evdev
from threading import Thread
import socket
import struct
from binascii import crc32
import time
import asyncio
import dbus
import json


def clamp(my_value, min_value, max_value):
    return max(min(my_value, max_value), min_value)


def abs_to_button(value):
    if value > 0.75*255:
        value = 255
    else:
        value = 0
    return value


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
        self.extend(bytes(struct.pack('<H', len(data) + 4)))

        self.extend([
            0x00, 0x00, 0x00, 0x00,  # place for CRC32
            0xff, 0xff, 0xff, 0xff,  # server ID
        ])

        self.extend(Message.Types[message_type])  # data type

        self.extend(data)

        # CRC32
        crc = crc32(bytes(self)) & 0xffffffff
        self[8:12] = bytes(struct.pack('<I', crc))


class SwitchDevice:
    def __init__(self, server, device, motion_device):
        self.server = server

        self.device = device
        self.motion_device = motion_device

        self.disconnected = False

        self.name = device.name
        self.serial = motion_device.uniq if motion_device.uniq != "" else "00:00:00:00:00:00"
        self.mac = [int("0x"+part, 16) for part in self.serial.split(":")]

        self.device_capabilities = device.capabilities(absinfo=False)

        self.state = {
            "left_analog_x": 0x00,
            "left_analog_y": 0x00,
            "right_analog_x": 0x00,
            "right_analog_y": 0x00,
            "dpad_up": 0x00,
            "dpad_down": 0x00,
            "dpad_left": 0x00,
            "dpad_right": 0x00,
            "button_cross": 0x00,
            "button_circle": 0x00,
            "button_square": 0x00,
            "button_triangle": 0x00,
            "button_l1": 0x00,
            "button_l2": 0x00,
            "button_l3": 0x00,
            "button_r1": 0x00,
            "button_r2": 0x00,
            "button_r3": 0x00,
            "button_share": 0x00,
            "button_options": 0x00,
            "button_ps": 0x00,
            "motion_y": 0x00,
            "motion_x": 0x00,
            "motion_z": 0x00,
            "orientation_roll": 0x00,
            "orientation_yaw": 0x00,
            "orientation_pitch": 0x00,
            "timestamp": 0x00,
            "battery": 0x00
        }

        self.keymap = None

        with open(self.name+'.json', 'r') as f:
            self.keymap = json.load(f)

        self.motion_x = 0
        self.motion_y = 0
        self.motion_z = 0

        self.accel_x = 0
        self.accel_y = 0
        self.accel_z = 0

        self.event_thread = Thread(target=self.handle_events)
        self.event_thread.daemon = True
        self.event_thread.start()

        self.motion_event_thread = Thread(target=self.handle_motion_events)
        self.motion_event_thread.daemon = True
        self.motion_event_thread.start()

    def handle_motion_events(self):
        if self.motion_device:
            try:
                asyncio.set_event_loop(asyncio.new_event_loop())
                for event in self.motion_device.read_loop():
                    if event.type == evdev.ecodes.SYN_REPORT:
                        self.server.report(self)

                        self.motion_x = 0
                        self.motion_y = 0
                        self.motion_z = 0
                    if event.type == evdev.ecodes.EV_ABS:
                        if event.code == evdev.ecodes.ABS_RX:
                            if abs(event.value) > 100:
                                if event.value > 0:
                                    self.motion_x += event.value - 100
                                else:
                                    self.motion_x += event.value + 100
                        if event.code == evdev.ecodes.ABS_RY:
                            if abs(event.value) > 100:
                                if event.value > 0:
                                    self.motion_y += event.value - 100
                                else:
                                    self.motion_y += event.value + 100
                        if event.code == evdev.ecodes.ABS_RZ:
                            if abs(event.value) > 100:
                                if event.value > 0:
                                    self.motion_z += event.value - 100
                                else:
                                    self.motion_z += event.value + 100
                        if event.code == evdev.ecodes.ABS_X:
                            self.accel_x = event.value
                        if event.code == evdev.ecodes.ABS_Y:
                            self.accel_y = event.value
                        if event.code == evdev.ecodes.ABS_Z:
                            self.accel_z = event.value
            except(OSError, RuntimeError) as e:
                print("Device motion disconnected: " + self.name)
                asyncio.get_event_loop().close()

    def handle_events(self):
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())

            for event in self.device.read_loop():
                if event.type == evdev.ecodes.SYN_REPORT:
                    self.server.report(self)
                if event.type == evdev.ecodes.EV_ABS:
                    for ps_key in self.keymap:
                        key_mine = self.keymap.get(ps_key, None)
                        if key_mine is None:
                            continue

                        if event.code == evdev.ecodes.ecodes.get(key_mine.replace("-", ""), None):
                            capabilities = self.device.capabilities()
                            axis = next(axis for axis in capabilities[3] if axis[0] == evdev.ecodes.ecodes.get(key_mine.replace("-", ""), None))
                            self.state[ps_key] = event.value / axis[1].max
                            self.state[ps_key] = clamp(self.state[ps_key], -1, 1)
                            if(key_mine[0] == "-"):
                                self.state[ps_key] = -self.state[ps_key]

                if event.type == evdev.ecodes.EV_KEY:
                    for ps_key in self.keymap:
                        if event.code == evdev.ecodes.ecodes.get(self.keymap.get(ps_key, None), None):
                            self.state[ps_key] = 0xFF if event.value == 1 else 0x00

        except(OSError, RuntimeError) as e:
            print("Device disconnected: " + self.name)
            self.server.report_clean(self)
            self.disconnected = True
            asyncio.get_event_loop().close()

    def get_battery_level(self):
        bus = dbus.SystemBus()
        upower = bus.get_object('org.freedesktop.UPower', '/org/freedesktop/UPower')

        upower_list = upower.get_dbus_method('EnumerateDevices', 'org.freedesktop.UPower')()

        for device in upower_list:
            dev = bus.get_object('org.freedesktop.UPower', device)
            iface = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
            properties = iface.GetAll("org.freedesktop.UPower.Device")

            if properties["Serial"] == self.serial:
                return properties["Percentage"]

        return None

    def get_report(self):
        report = self.state

        battery = self.get_battery_level()

        if battery is None:
            report["battery"] = 0x00
        elif battery < 10:
            report["battery"] = 0x01
        elif battery < 25:
            report["battery"] = 0x02
        elif battery < 75:
            report["battery"] = 0x03
        elif battery < 90:
            report["battery"] = 0x04
        else:
            report["battery"] = 0x05

        self.state["battery"] = report["battery"]

        if self.device is None:
            return report

        return report


class UDPServer:
    def __init__(self, host='', port=26760):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.counter = 0
        self.clients = dict()
        self.slots = [None, None, None, None]
        self.locks = [asyncio.Lock(), asyncio.Lock(), asyncio.Lock(), asyncio.Lock()]
        self.lock = asyncio.Lock()

    def _res_ports(self, index):
        data = [
            index,  # pad id
            0x00,  # state (disconnected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # Mac
            0x00,  # battery (charged)
            0x00,  # ?
        ]

        if self.slots[index]:
            device = self.slots[index]
            data = [
                index,  # pad id
                0x02,  # state (connected)
                0x03,  # model (generic)
                0x01,  # connection type (usb)
                device.mac[0], device.mac[1], device.mac[2],  # MAC1
                device.mac[3], device.mac[4], device.mac[5],  # MAC2
                device.state["battery"],  # battery (charged)
                0x00,  # ?
            ]

        return Message('ports', data)

    @staticmethod
    def _compat_ord(value):
        return ord(value) if sys.version_info < (3, 0) else value

    def _req_ports(self, message, address):
        requests_count = struct.unpack("<i", message[20:24])[0]
        for i in range(requests_count):
            index = self._compat_ord(message[24 + i])
            self.sock.sendto(bytes(self._res_ports(index)), address)

    def _req_data(self, message, address):
        flags = self._compat_ord(message[24])
        reg_id = self._compat_ord(message[25])
        # reg_mac = message[26:32]

        if flags == 0 and reg_id == 0:  # TODO: Check MAC
            if address not in self.clients:
                print('[udp] Client connected: {0[0]}:{0[1]}'.format(address))

            self.clients[address] = time.time()

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

    def _res_data(self, message):
        now = time.time()
        for address, timestamp in self.clients.copy().items():
            if now - timestamp < 5:
                self.sock.sendto(message, address)
            else:
                print('[udp] Client disconnected: {0[0]}:{0[1]}'.format(address))
                del self.clients[address]

    def report(self, device):
        if device is None:
            return None

        if device.device is None:
            return None

        i = self.slots.index(device) if device in self.slots else -1

        if i == -1:
            return None

        device_state = device.get_report()

        data = [
            i & 0xff,  # pad id
            0x02 if device.device is not None else 0x00,  # state (connected)
            0x02,  # model (generic)
            0x02,  # connection type (usb)
            device.mac[0], device.mac[1], device.mac[2],  # MAC1
            device.mac[3], device.mac[4], device.mac[5],  # MAC2
            device_state["battery"],  # battery (charged)
            0x01  # is active (true)
        ]

        data.extend(struct.pack('<I', self.counter))
        self.counter += 1

        buttons1 = 0x00
        buttons1 |= int(abs_to_button(device_state.get("button_share", 0x00))/255)
        buttons1 |= int(abs_to_button(device_state.get("button_l3", 0x00))/255) << 1
        buttons1 |= int(abs_to_button(device_state.get("button_r3", 0x00))/255) << 2
        buttons1 |= int(abs_to_button(device_state.get("button_options", 0x00))/255) << 3
        buttons1 |= int(abs_to_button(device_state.get("dpad_up", 0x00))/255) << 4
        buttons1 |= int(abs_to_button(device_state.get("dpad_right", 0x00))/255) << 5
        buttons1 |= int(abs_to_button(device_state.get("dpad_down", 0x00))/255) << 6
        buttons1 |= int(abs_to_button(device_state.get("dpad_left", 0x00))/255) << 7

        buttons2 = 0x00
        buttons2 |= int(abs_to_button(device_state.get("button_l2", 0x00)/255))
        buttons2 |= int(abs_to_button(device_state.get("button_r2", 0x00)/255)) << 1
        buttons2 |= int(abs_to_button(device_state.get("button_l1", 0x00)/255)) << 2
        buttons2 |= int(abs_to_button(device_state.get("button_r1", 0x00)/255)) << 3

        data.extend([
            buttons1,
            buttons2,
            abs_to_button(device_state.get("button_ps", 0x00) * 127 + 128),  # PS
            0x00,  # Touch

            int(device_state.get("left_analog_x", 0x00) * 127) + 128,  # position left x
            int(device_state.get("left_analog_y", 0x00) * 127) + 128,  # position left y
            int(device_state.get("right_analog_x", 0x00) * 127) + 128,  # position right x
            int(device_state.get("right_analog_y", 0x00) * 127) + 128,  # position right y

            abs_to_button(device_state.get("dpad_left", 0x00) * 127 + 128),  # dpad left
            abs_to_button(device_state.get("dpad_down", 0x00) * 127 + 128),  # dpad down
            abs_to_button(device_state.get("dpad_right", 0x00) * 127 + 128),  # dpad right
            abs_to_button(device_state.get("dpad_up", 0x00) * 127 + 128),  # dpad up

            abs_to_button(device_state.get("button_square", 0x00) * 127 + 128),  # square
            abs_to_button(device_state.get("button_cross", 0x00) * 127 + 128),  # cross
            abs_to_button(device_state.get("button_circle", 0x00) * 127 + 128),  # circle
            abs_to_button(device_state.get("button_triangle", 0x00) * 127 + 128),  # triange

            abs_to_button(device_state.get("button_r1", 0x00) * 127 + 128),  # r1
            abs_to_button(device_state.get("button_l1", 0x00) * 127 + 128),  # l1

            abs_to_button(device_state.get("button_r2", 0x00) * 127 + 128),  # r2
            abs_to_button(device_state.get("button_l2", 0x00) * 127 + 128),  # l2

            0x00,  # track pad first is active (false)
            0x00,  # track pad first id

            0x00, 0x00,  # trackpad first x
            0x00, 0x00,  # trackpad first y

            0x00,  # track pad second is active (false)
            0x00,  # track pad second id

            0x00, 0x00,  # trackpad second x
            0x00, 0x00,  # trackpad second y
        ])

        data.extend(bytes(struct.pack('<Q', int(time.time() * 10**6))))

        if device.motion_device is not None:
            if device.motion_device.name == "Nintendo Switch Pro Controller IMU":
                sensors = [
                    device.accel_y / 4096,
                    - device.accel_z / 4096,
                    device.accel_x / 4096,
                    - device.motion_y * 180 / 3.14 / 1000,
                    - device.motion_z * 180 / 3.14 / 1000,
                    device.motion_x * 180 / 3.14 / 1000,
                ]
            elif device.motion_device.name == "Nintendo Switch Right Joy-Con IMU":
                sensors = [
                    - device.accel_y / 4096,
                    device.accel_z / 4096,
                    device.accel_x / 4096,
                    device.motion_y * 180 / 3.14 / 1000,
                    device.motion_z * 180 / 3.14 / 1000,
                    device.motion_x * 180 / 3.14 / 1000,
                ]
            else:
                sensors = [
                    device.accel_y / 4096,
                    - device.accel_z / 4096,
                    device.accel_x / 4096,
                    - device.motion_y * 180 / 3.14 / 1000,
                    - device.motion_z * 180 / 3.14 / 1000,
                    device.motion_x * 180 / 3.14 / 1000,
                ]
        else:
            sensors = [0, 0, 0, 0, 0, 0]

        for sensor in sensors:
            data.extend(struct.pack('<f', float(sensor)))

        self._res_data(bytes(Message('data', data)))

    def report_clean(self, device):
        i = self.slots.index(device) if device in self.slots else -1

        data = [
            i & 0xff,  # pad id
            0x00,  # state (disconnected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # Mac
            0x00,  # battery (charged)
            0x00,  # ?
        ]

        self._res_data(bytes(Message('data', data)))

    def handle_devices(self):
        asyncio.set_event_loop(asyncio.new_event_loop())

        print("Looking for Nintendo Switch controllers...")

        error = False
        while not error:
            try:
                evdev_devices = [evdev.InputDevice(path) for path in evdev.list_devices()]

                for d in evdev_devices:
                    if d.name == "Nintendo Switch Left Joy-Con" or \
                            d.name == "Nintendo Switch Right Joy-Con" or \
                            d.name == "Nintendo Switch Pro Controller" or \
                            d.name == "Nintendo Switch Combined Joy-Cons":
                        found = True if any(my_device.device == d for my_device in self.slots if my_device is not None) else False

                        if not found:
                            motion_d = None

                            for dd in evdev_devices:
                                if dd.uniq == d.uniq and dd != d:
                                    motion_d = dd
                                    break

                            if motion_d is None:
                                print("Select motion provider for "+d.name+": ")
                                for i, dd in enumerate(evdev_devices):
                                    print(str(i) + " " + dd.name + " " + dd.uniq)
                                motion_d = evdev_devices[int(input(""))]

                            for i in range(4):
                                if self.slots[i] is None:
                                    try:
                                        self.slots[i] = SwitchDevice(self, d, motion_d)
                                        print("Found "+d.name+" - "+d.uniq)
                                    except Exception as e:
                                        error = True
                                    break

                            self.print_slots()

                for i in range(4):
                    if self.slots[i] is not None and self.slots[i].disconnected is True:
                        self.slots[i] = None
                        self.print_slots()

                time.sleep(0.2)  # sleep for 0.2 seconds

            except:
                pass

    def print_slots(self):
        slots_print = []

        for i in range(4):
            if self.slots[i] is None:
                slots_print.append("0")
            else:
                if "Left" in self.slots[i].name:
                    slots_print.append("L")
                elif "Right" in self.slots[i].name:
                    slots_print.append("R")
                elif "Combined" in self.slots[i].name:
                    slots_print.append("L+R")
                else:
                    slots_print.append("Pro")

        print("Slots: "+str(slots_print))

    def _worker(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        while True:
            self._handle_request(self.sock.recvfrom(1024))

    def start(self):
        self.thread = Thread(target=self._worker)
        self.thread.daemon = True
        self.thread.start()

        self.device_thread = Thread(target=self.handle_devices)
        self.device_thread.daemon = True
        self.device_thread.start()

        self.device_thread.join()

    def stop(self, sig=None, frame=None, err=None):
        if sig is not None:
            print("Stopping server")
        self.report_clean(self)
        self.disconnected = True
        asyncio.get_event_loop().close()
        print(err)
        if err:
            raise Exception(err)




server = UDPServer('127.0.0.1', 26760)

# Handle CTRL+C and systemctl stop default signal
signal.signal(signal.SIGINT, server.stop)
signal.signal(signal.SIGTERM, server.stop)

try:
    server.start()
except Exception as e:
    print("hey")
    sys.exit(e)
