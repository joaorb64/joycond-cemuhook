import sys
import evdev
import pyudev
from threading import Thread, Event
import socket
import struct
from binascii import crc32
import time
import asyncio
import signal
import dbus
import json
import argparse
import subprocess
from termcolor import colored
from collections import OrderedDict
from os.path import basename

MAX_PADS = 4

def print_verbose(str):
    global args
    if args.verbose:
        print(colored("Debug: ", "red", attrs=["bold"])+str)

def clamp(my_value, min_value, max_value):
    return max(min(my_value, max_value), min_value)

def abs_to_button(value):
    if value > 0.75:
        value = 255
    else:
        value = 0
    return value

def get_led_status(device):
    leds = {}
    context = pyudev.Context()
    udev_device = context.list_devices(subsystem='input').match_attribute('uniq', device.uniq.encode()).match_attribute('name', device.name.encode())

    # should match only one device
    udev_device = next(iter(udev_device))

    # combined device has no parent and would match random leds in the system
    if udev_device.parent is None:
        return leds

    udev_leds = context.list_devices(subsystem='leds').match_parent(udev_device.parent)
    for led in udev_leds:
        name = led.sys_name.split(':')[-1]
        status = led.attributes.get('brightness').decode()
        leds[name] = status
    return leds

def get_player_id(device):
    player = 0
    for led, status in sorted(get_led_status(device).items()):
        if "player" in led:
            if status == '1':
                player += 1

            # Should prevent reading an incorrect player id during a temporary led state in some cases
            else:
                break

    # Combined devices don't have real leds and use evdev API instead
    if not player:
        player = len(device.leds())
    return player

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
    def __init__(self, server, device, motion_device, motion_only=False):
        self.server = server

        self.device = device
        self.motion_device = motion_device

        self.disconnected = False

        self.name = device.name
        self.serial = motion_device.uniq if motion_device.uniq != "" else "00:00:00:00:00:00"
        self.mac = [int("0x"+part, 16) for part in self.serial.split(":")]

        self.led_status = get_led_status(self.device)

        if not self.led_status:
            self.led_status = get_led_status(self.motion_device)

        self.player_id = get_player_id(self.device)

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

        with open('profiles/'+self.name+'.json', 'r') as f:
            self.keymap = json.load(f)

        self.motion_x = 0
        self.motion_y = 0
        self.motion_z = 0
        
        self.accel_x = 0
        self.accel_y = 0
        self.accel_z = 0

        self.motion_only = motion_only

        self.battery_level = None
        self.battery_state = None
        self.dbus_interface = None
        self.dbus_properties_interface = None
        self.get_battery_dbus_interface()

        self.thread = Thread(target=self._worker)
        self.thread.daemon = True
        self.thread.start()

    def _worker(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Motion reading task
        tasks = {asyncio.ensure_future(self._handle_motion_events())}

        # Input reading task
        if not self.motion_only:
            tasks.add(asyncio.ensure_future(self._handle_events()))

        # Battery level reading task
        if self.dbus_interface and self.dbus_properties_interface:
            tasks.add(asyncio.ensure_future(self._get_battery_level()))

        # Listen to termination request task
        tasks.add(asyncio.ensure_future(self._wait_for_termination()))

        # Start all tasks, stop at the first completed task
        done, pending = asyncio.get_event_loop().run_until_complete(asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED))

        # Cancel all other tasks
        for task in pending:
            task.cancel()

        # Wait for all tasks to finish
        self._loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(self._loop)))
        asyncio.get_event_loop().close()

        self._disconnect()

    def _disconnect(self):
        print(F"Device disconnected: {self.name}")
        self.server.report_clean(self)
        self.disconnected = True

    async def _wait_for_termination(self):
        self._terminate_event = asyncio.Event()
        try:
            await self._terminate_event.wait()
        except asyncio.CancelledError:
            return

    def terminate(self):
        self._terminate_event.set()
        self.thread.join()

    async def _handle_motion_events(self):
        print_verbose(F"Motion events task started {self.motion_device}")
        try:
            async for event in self.motion_device.async_read_loop():
                if event.type == evdev.ecodes.SYN_REPORT:
                    self.server.report(self, True)
                    self.motion_x = 0
                    self.motion_y = 0
                    self.motion_z = 0
                elif event.type == evdev.ecodes.EV_ABS:
                    # Get info about the axis we're reading the event from
                    axis = self.motion_device.absinfo(event.code)

                    if event.code == evdev.ecodes.ABS_RX:
                        self.motion_x += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_RY:
                        self.motion_y += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_RZ:
                        self.motion_z += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_X:
                        self.accel_x = event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_Y:
                        self.accel_y = event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_Z:
                        self.accel_z = event.value / axis.resolution
        except (asyncio.CancelledError, OSError) as e:
            print_verbose("Motion events task ended")

    async def _handle_events(self):
        print_verbose("Input events task started")
        try:
            async for event in self.device.async_read_loop():
                if event.type == evdev.ecodes.SYN_REPORT:
                    self.server.report(self)
                if event.type == evdev.ecodes.EV_ABS:
                    for ps_key in self.keymap:
                        key_mine = self.keymap.get(ps_key, None)
                        if key_mine == None:
                            continue

                        if event.code == evdev.ecodes.ecodes.get(key_mine.replace("-", ""), None):
                            axis = self.device.absinfo(evdev.ecodes.ecodes.get(key_mine.replace("-", "")))
                            self.state[ps_key] = event.value / axis.max
                            self.state[ps_key] = clamp(self.state[ps_key], -1, 1)
                            if(key_mine[0] == "-"):
                                self.state[ps_key] = -self.state[ps_key]

                if event.type == evdev.ecodes.EV_KEY:
                    for ps_key in self.keymap:
                        if event.code == evdev.ecodes.ecodes.get(self.keymap.get(ps_key, None), None):
                            self.state[ps_key] = 0xFF if event.value == 1 else 0x00
        except (asyncio.CancelledError, OSError) as e:
            print_verbose("Input events task ended")
    
    def get_battery_dbus_interface(self):
        bus = dbus.SystemBus()
        upower = bus.get_object('org.freedesktop.UPower', '/org/freedesktop/UPower')

        upower_list = upower.get_dbus_method('EnumerateDevices', 'org.freedesktop.UPower')()

        for device in upower_list:
            dev = bus.get_object('org.freedesktop.UPower', device)

            dbus_interface = dbus.Interface(dev, 'org.freedesktop.UPower.Device')
            dbus_interface.Refresh()

            dbus_properties_interface = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
            properties = dbus_properties_interface.GetAll("org.freedesktop.UPower.Device")

            if properties["Serial"] == self.serial:
                self.dbus_interface = dbus_interface
                self.dbus_properties_interface = dbus_properties_interface
                self.battery_level = properties["Percentage"]
                self.battery_state = properties["State"]
                print_verbose("Found dbus interface for battery level reading. Value: "+str(self.battery_level))
                return True
        return False
    
    async def _get_battery_level(self):
        print_verbose("Battery level reading thread started")
        try:
            while self.dbus_interface != None:
                self.dbus_interface.Refresh()
                properties = self.dbus_properties_interface.GetAll("org.freedesktop.UPower.Device")
                if properties["Percentage"] != self.battery_level:
                    print_verbose("Battery level changed")
                    self.battery_level = properties["Percentage"]
                    self.battery_state = properties["State"]
                    self.server.print_slots()
                await asyncio.sleep(30)
        except (asyncio.CancelledError, Exception) as e:
            print_verbose("Battery level reading task ended")
            self.battery_level = None
            self.battery_state = None
    
    def get_report(self):
        report = self.state

        if self.battery_level == None:
            report["battery"] = 0x00
        elif self.battery_level < 10:
            report["battery"] = 0x01
        elif self.battery_level < 25:
            report["battery"] = 0x02
        elif self.battery_level < 75:
            report["battery"] = 0x03
        elif self.battery_level < 90:
            report["battery"] = 0x04
        else:
            report["battery"] = 0x05
        
        self.state["battery"] = report["battery"]

        if self.device == None:
            return report
        
        return report

class UDPServer:
    def __init__(self, host='', port=26760):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        print_verbose("Started UDP server with ip "+str(host)+", port "+str(port))
        self.counter = 0
        self.clients = dict()
        self.slots = [None] * MAX_PADS
        self.blacklisted = []
        self.stop_event = Event()

    def _res_ports(self, index):
        data = [
            index,  # pad id
            0x00,  # state (disconnected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, # Mac
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
                *device.mac,  # MAC
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
        slot_id = self._compat_ord(message[21])
        # reg_mac = message[26:32]

        if flags == 0 and reg_id == 0:  # TODO: Check MAC
            if address not in self.clients:
                print('[udp] Client connected: {0[0]}:{0[1]}'.format(address))

                self.clients[address] = {
                    "timestamp": time.time(),
                    "controllers": [0,0,0,0]
                }
            else:
                self.clients[address]["timestamp"] = time.time()
                self.clients[address]["controllers"][slot_id] = 1
    
    def _res_data(self, controller_index, message):
        now = time.time()
        for address, data in self.clients.copy().items():
            if data["controllers"][controller_index] == 1:
                if now - data["timestamp"] < 5:
                    self.sock.sendto(message, address)
                else:
                    print('[udp] Client disconnected: {0[0]}:{0[1]}'.format(address))
                    del self.clients[address]
    
    def _handle_request(self, request):
        message, address = request

        # Ignore empty messages (sent by sock_stop)
        if not message:
            return

        # client_id = message[12:16]
        msg_type = message[16:20]

        if msg_type == Message.Types['version']:
            return
        elif msg_type == Message.Types['ports']:
            self._req_ports(message, address)
        elif msg_type == Message.Types['data']:
            self._req_data(message, address)
        else:
            print('[udp] Unknown message type: ' + str(msg_type))

    def report(self, device, report_motion=False):
        if device == None:
            return None
        
        if device.device == None:
            return None
        
        i = self.slots.index(device) if device in self.slots else -1

        if i == -1:
            return None
        
        device_state = device.get_report()

        data = [
            i & 0xff,  # pad id
            0x02 if device.device != None else 0x00,  # state (connected)
            0x02,  # model (generic)
            0x02,  # connection type (usb)
            *device.mac,  # MAC
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
        buttons2 |= int(abs_to_button(device_state.get("button_l2", 0x00))/255)
        buttons2 |= int(abs_to_button(device_state.get("button_r2", 0x00))/255) << 1
        buttons2 |= int(abs_to_button(device_state.get("button_l1", 0x00))/255) << 2
        buttons2 |= int(abs_to_button(device_state.get("button_r1", 0x00))/255) << 3
        buttons2 |= int(abs_to_button(device_state.get("button_triangle", 0x00))/255) << 4
        buttons2 |= int(abs_to_button(device_state.get("button_circle", 0x00))/255) << 5
        buttons2 |= int(abs_to_button(device_state.get("button_cross", 0x00))/255) << 6
        buttons2 |= int(abs_to_button(device_state.get("button_square", 0x00))/255) << 7

        data.extend([
            buttons1,
            buttons2,
            abs_to_button(device_state.get("button_ps", 0x00)),  # PS
            0x00,  # Touch

            int(device_state.get("left_analog_x", 0x00) * 127) + 128,  # position left x
            int(device_state.get("left_analog_y", 0x00) * 127) + 128,  # position left y
            int(device_state.get("right_analog_x", 0x00) * 127) + 128,  # position right x
            int(device_state.get("right_analog_y", 0x00) * 127) + 128,  # position right y

            abs_to_button(device_state.get("dpad_left", 0x00)),  # dpad left
            abs_to_button(device_state.get("dpad_down", 0x00)),  # dpad down
            abs_to_button(device_state.get("dpad_right", 0x00)),  # dpad right
            abs_to_button(device_state.get("dpad_up", 0x00)),  # dpad up

            abs_to_button(device_state.get("button_square", 0x00)),  # square
            abs_to_button(device_state.get("button_cross", 0x00)),  # cross
            abs_to_button(device_state.get("button_circle", 0x00)),  # circle
            abs_to_button(device_state.get("button_triangle", 0x00)),  # triange

            abs_to_button(device_state.get("button_r1", 0x00)),  # r1
            abs_to_button(device_state.get("button_l1", 0x00)),  # l1

            abs_to_button(device_state.get("button_r2", 0x00)),  # r2
            abs_to_button(device_state.get("button_l2", 0x00)),  # l2

            0x00,  # track pad first is active (false)
            0x00,  # track pad first id

            0x00, 0x00,  # trackpad first x
            0x00, 0x00,  # trackpad first y

            0x00,  # track pad second is active (false)
            0x00,  # track pad second id

            0x00, 0x00,  # trackpad second x
            0x00, 0x00,  # trackpad second y
        ])

        data.extend(bytes(struct.pack('<Q', time.time_ns() // 1000)))

        if device.motion_device != None:
            sensors = [
                # Acceleration in g's
                device.accel_y,
                - device.accel_z,
                device.accel_x,
                # Gyro rotation in deg/s
                - device.motion_y,
                - device.motion_z,
                device.motion_x,
            ]

            if report_motion == False:
                sensors[3] = 0
                sensors[4] = 0
                sensors[5] = 0
        else:
            sensors = [0, 0, 0, 0, 0, 0]

        for sensor in sensors:
            data.extend(struct.pack('<f', float(sensor)))
        
        self._res_data(i, bytes(Message('data', data)))
    
    def report_clean(self, device):
        i = self.slots.index(device) if device in self.slots else -1

        data = [
            i & 0xff,  # pad id
            0x00,  # state (disconnected)
            0x03,  # model (generic)
            0x01,  # connection type (usb)
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, # Mac
            0x00,  # battery (charged)
            0x00,  # ?
        ]

        self._res_data(i, bytes(Message('data', data)))
    
    def add_device(self, device, motion_device, motion_only=False):
        # Find an empty slot for the new device
        for i, slot in enumerate(self.slots):
            if not slot:
                self.slots[i] = SwitchDevice(self, device, motion_device, motion_only)
                return i

        # All four slots have been allocated
        print("Unable to use device [" + d.name + "]: Slots full")
        self.blacklisted.append(d)
        return MAX_PADS

    def add_devices(self, device, motion_devices, motion_only=False):
        if not motion_devices:
            return

        # For the first motion device, start both input thread and motion thread
        self.add_device(device, motion_devices.pop(0), motion_only)

        # For additional motion devices, start only motion thread to avoid 'device busy' errors
        for motion_device in motion_devices:
            self.add_device(device, motion_device, True)

    def handle_devices(self):
        asyncio.set_event_loop(asyncio.new_event_loop())

        print("Looking for Nintendo Switch controllers...")
        
        while not self.stop_event.is_set():
            try:
                evdev_devices = [evdev.InputDevice(path) for path in evdev.list_devices()]

                # Filter devices that were already assigned or couldn't be assigned
                evdev_devices = [d for d in evdev_devices if d not in self.blacklisted and not any(d in [dd.device, dd.motion_device] for dd in self.slots if dd)]

                valid_device_names = ["Nintendo Switch Left Joy-Con",
                                      "Nintendo Switch Right Joy-Con",
                                      "Nintendo Switch Pro Controller",
                                      "Nintendo Switch Combined Joy-Cons"]

                taken_slots = lambda: sum(d is not None for d in self.slots)

                # players 1-4, 0 for invalid devices
                players = {i:[] for i in range(MAX_PADS+1)}
                for d in evdev_devices:
                    players[get_player_id(d)].append(d)

                active_devices = taken_slots()

                del players[0]
                for player, devices in sorted(players.items()):
                    if not devices:
                        continue

                    # This might happen if there are more than 4 players
                    # This can lead to buggy behaviour and should be blacklisted for now
                    elif len(devices) > 3:
                        print(F"More than four players detected. Ignoring player {player}.")
                        self.blacklisted.extend(devices)
                        continue

                    # Could happen when one Joy-Con in a combined pair is disconnected and then reconnected
                    previously_assigned = next((slot for slot in self.slots if slot and player == slot.player_id), None)
                    if previously_assigned:
                        self.add_devices(previously_assigned.device, devices, not previously_assigned.motion_only)
                        continue

                    # Lone device
                    if all(d.uniq == devices[0].uniq for d in devices):
                        devices.sort(key=lambda d: d.name in valid_device_names, reverse=True)
                        try:
                            device = next(d for d in devices if d.name in valid_device_names)

                        # Device is not yet 'paired'
                        except StopIteration:
                            continue
                        devices.remove(device)
                        motion_devices = devices

                    # Paired Joy-Cons
                    else:
                        try:
                            device = next(d for d in devices if "Combined" in d.name)
                            devices.remove(device)

                        # Added for compatibility with older versions of joycond
                        except StopIteration:
                            combined_devices = [d for d in evdev_devices if d.name == "Nintendo Switch Combined Joy-Cons"]

                            # Devices are not yet 'paired'
                            if not combined_devices:
                                continue

                            # Sort combined devices by creation time.
                            # This is the best guess we have to match combined device to it's individual Joy-Cons
                            if len(combined_devices) > 1:
                                context = pyudev.Context()
                                combined_devices.sort(key=lambda d: next(iter(context.list_devices(sys_name=basename(d.path)))).time_since_initialized)

                            device = combined_devices.pop(0)

                        # Right Joy-Con is mapped first
                        motion_devices = sorted(devices, key=lambda d: "Right" in d.name, reverse=True)

                        if args.right_only or args.left_only:
                            removed = "Right" if args.left_only else "Left"
                            removed_device = next((d for d in motion_devices if removed in d.name), None)
                            if removed_device:
                                motion_devices.remove(removed_device)
                                self.blacklisted.append(removed_device)

                    self.add_devices(device, motion_devices)

                if active_devices != taken_slots():
                    self.print_slots()
                    active_devices = taken_slots()

                # Detect disconnected devices
                for i, slot in enumerate(self.slots):
                    if slot and slot.disconnected:
                        self.slots[i] = None

                if active_devices != taken_slots():
                    self.print_slots()
                
                self.stop_event.wait(0.2) # sleep for 0.2 seconds to avoid 100% cpu usage
            except Exception as e:
                print(e)
                    
    
    def print_slots(self):
        print(colored("======================== Slots ========================", attrs=["bold"]))

        print (colored("{:<14} {:<12} {:<12} {:<12}", attrs=["bold"])
            .format("Device", "LED status", "Battery Lv", "MAC Addr"))

        for i, slot in enumerate(self.slots):
            if not slot:
                print(str(i+1)+" ❎ ")
            else:
                device = str(i+1)+" "
                if "Left" in slot.name:
                    device += "🕹️ L"
                elif "Right" in slot.name:
                    device += "🕹️ R"
                elif "Combined" in slot.name:
                    device += "🎮 L+R"
                else:
                    device += "🎮 Pro"

                leds = ""
                for led, status in sorted(slot.led_status.items()):
                    if "player" in led:
                        leds += "■ " if status == '1' else "□ "

                if not leds:
                    leds = "?"

                if slot.battery_level:
                    battery = F"{str(slot.battery_level)} {chr(ord('▁') + int(slot.battery_level * 7 / 100))}"
                else:
                    battery = "❌"
                
                mac = slot.serial

                # print device after calculating alignment because the gamepad symbols cause alignment errors
                print(F'{"":<14} {colored(F"{leds:<12}", "green")} {colored(F"{battery:<12}", "green")} {mac:<12}\r{device}')

        print(colored("=======================================================", attrs=["bold"]))

    def _worker(self):
        while not self.stop_event.is_set():
            self._handle_request(self.sock.recvfrom(1024))
        self.sock.close()

    def start(self):
        self.thread = Thread(target=self._worker)
        self.thread.daemon = True
        self.thread.start()

        self.device_thread = Thread(target=self.handle_devices)
        self.device_thread.daemon = True
        self.device_thread.start()

    def stop(self):
        for slot in self.slots:
            if slot:
                slot.terminate()

        self.stop_event.set()

        # Send message to sock to trigger sock.recvfrom
        sock_stop = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_stop.sendto(b'', self.sock.getsockname())
        sock_stop.close()

        self.thread.join()
        self.device_thread.join()

parser = argparse.ArgumentParser()
parser.add_argument("-v", "--verbose", help="show debug messages", action="store_true")
parser.add_argument("-ip", "--ip", help="set custom port, default is 127.0.0.1", default="127.0.0.1")
parser.add_argument("-p", "--port", help="set custom port, default is 26760", type=int, default=26760)

select_motion = parser.add_mutually_exclusive_group()
select_motion.add_argument("-l", "--left-only", help="use only left Joy-Cons for combined device motion", action="store_true")
select_motion.add_argument("-r", "--right-only", help="use only right Joy-Cons for combined device motion", action="store_true")

args = parser.parse_args()

# Check if hid_nintendo module is installed
process = subprocess.Popen(["modinfo", "hid_nintendo"], stdout=subprocess.DEVNULL)
process.communicate()
hid_nintendo_installed = process.returncode

if hid_nintendo_installed == 1:
    print("Seems like hid_nintendo is not installed.")
    exit()

# Check if hid_nintendo module is loaded
process = subprocess.Popen(["/bin/sh", "-c", 'lsmod | grep hid_nintendo'], stdout=subprocess.DEVNULL)
process.communicate()
hid_nintendo_loaded = process.returncode

if hid_nintendo_loaded == 1:
    print("Seems like hid_nintendo is not loaded. Load it with 'sudo modprobe hid_nintendo'.")
    exit()

server = UDPServer(args.ip, args.port)
server.start()

def signal_handler(signal, frame):
    print("Stopping server...")
    server.stop()

signal.signal(signal.SIGINT, signal_handler)
signal.pause()
