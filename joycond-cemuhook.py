import sys
import evdev
import pyudev
import threading
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
import os.path
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

loop = GLib.MainLoop()


def print_verbose(str):
    global args
    if args.verbose:
        print(colored("Debug: ", "red", attrs=["bold"]) + str)


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
    udev_device = context.list_devices(subsystem='input').match_attribute('uniq', device.uniq.encode()).match_attribute(
        'name', device.name.encode())

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
    try:
        leds = device.items()
    except AttributeError:
        leds = get_led_status(device).items()

    # Combined devices don't have real leds and use evdev API instead
    if not leds:
        try:
            return len(device.leds())
        except AttributeError:
            pass

    player = 0
    for led, status in sorted(leds):
        if "player" in led:
            if status == '1':
                player += 1

            # Should prevent reading an incorrect player id during a temporary led state in some cases
            else:
                break

    return player


class BaseMessage(bytearray):
    Types = dict(version=bytes([0x00, 0x00, 0x10, 0x00]),
                 ports=bytes([0x01, 0x00, 0x10, 0x00]),
                 data=bytes([0x02, 0x00, 0x10, 0x00]))

    def __init__(self, message_type, data):
        self.extend([
            0x44, 0x53, 0x55, 0x53,  # DSUS,
            0xE9, 0x03,  # protocol version (1001),
            *struct.pack('<H', len(data) + 4),  # data length
            0x00, 0x00, 0x00, 0x00,  # place for CRC32
            0xff, 0xff, 0xff, 0xff,  # server ID
            *BaseMessage.Types[message_type],  # data type
            *data
        ])

        # CRC32
        crc = crc32(self) & 0xffffffff
        self[8:12] = struct.pack('<I', crc)


class Message(BaseMessage):
    def __init__(self, message_type, device, data=[0]):
        index = getattr(device, 'index', device) & 0xff

        # Shared response for ports and data messages
        data = [
                   index,  # pad id
                   0x02 if getattr(device, 'connected', False) else 0x00,  # state (disconnected/connected)
                   0x02,  # model (full gyro)
                   getattr(device, 'connection_type', 0x00),  # connection type (n.a./usb/bluetooth)
                   *(getattr(device, 'mac', [0x00] * 6)),  # MAC
                   getattr(device, 'battery_status', 0x00)  # battery status
               ] + data

        super(Message, self).__init__(message_type, data)


class SwitchDevice:
    def __init__(self, server, index, device, motion_device, motion_only=False):
        self.server = server
        self.index = index
        self.device = device
        self.motion_device = motion_device
        self.motion_only = motion_only

        self.name = device.name
        self.serial = motion_device.uniq if motion_device.uniq else "00:00:00:00:00:00"
        self.mac = [int(part, 16) for part in self.serial.split(":")]

        # Connection type (1 = USB, 2 = Bluetooth)
        self.connection_type = 0x01 if "Grip" in motion_device.name else 0x02

        self.led_status = get_led_status(device)

        if not self.led_status:
            self.led_status = get_led_status(motion_device)

        self.player_id = get_player_id(self.led_status)

        with open(os.path.join('profiles', self.name + '.json')) as profile:
            original_keymap = json.load(profile)
            self.keymap = {evdev.ecodes.ecodes[ecode.lstrip('-')]: [] for ps_key, ecode in original_keymap.items() if
                           ecode is not None}
            for ps_key, ecode in original_keymap.items():
                if ecode is not None:
                    prefix = '-' if ecode.startswith('-') else ''
                    self.keymap[evdev.ecodes.ecodes[ecode.lstrip('-')]].append(prefix + ps_key)

        self.state = {ps_key.lstrip('-'): 0x00 for ps_key in original_keymap.keys()}

        self.state.update(accel_x=0.0, accel_y=0.0, accel_z=0.0,
                          motion_x=0.0, motion_y=0.0, motion_z=0.0)

        self.battery = None
        self.dbus_interface, self.dbus_properties_interface = self.get_battery_dbus_interface()

        self.thread = threading.Thread(target=self._worker)
        self.thread.start()

    def _worker(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Motion reading task
        tasks = {asyncio.ensure_future(self._handle_motion_events(), loop=self._loop)}

        # Input reading task
        if not self.motion_only:
            tasks.add(asyncio.ensure_future(self._handle_events(), loop=self._loop))

        # Listen to termination request task
        tasks.add(asyncio.ensure_future(self._wait_for_termination(), loop=self._loop))

        # Start all tasks, stop at the first completed task
        done, pending = self._loop.run_until_complete(asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED))

        # Cancel all other tasks
        for task in pending:
            task.cancel()

        # Wait for all tasks to finish
        self._loop.run_until_complete(asyncio.wait(pending))
        self._loop.close()

        self.device.close()
        self.motion_device.close()
        print(F"Device disconnected: {self.name}")
        self.server.report_clean(self)

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
                    self.state['motion_x'] = 0.0
                    self.state['motion_y'] = 0.0
                    self.state['motion_z'] = 0.0
                elif event.type == evdev.ecodes.EV_ABS:
                    # Get info about the axis we're reading the event from
                    axis = self.motion_device.absinfo(event.code)

                    if event.code == evdev.ecodes.ABS_RX:
                        self.state['motion_x'] += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_RY:
                        self.state['motion_y'] += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_RZ:
                        self.state['motion_z'] += event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_X:
                        self.state['accel_x'] = event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_Y:
                        self.state['accel_y'] = event.value / axis.resolution
                    if event.code == evdev.ecodes.ABS_Z:
                        self.state['accel_z'] = event.value / axis.resolution
        except (asyncio.CancelledError, OSError) as e:
            print_verbose("Motion events task ended")

    async def _handle_events(self):
        print_verbose("Input events task started")
        try:
            async for event in self.device.async_read_loop():
                if event.type == evdev.ecodes.SYN_REPORT:
                    self.server.report(self)

                elif event.type == evdev.ecodes.EV_KEY:
                    try:
                        ps_keys = self.keymap[event.code]
                    except KeyError:
                        continue

                    for ps_key in ps_keys:
                        self.state[ps_key] = 0xFF if event.value else 0x00

                elif event.type == evdev.ecodes.EV_ABS:
                    try:
                        ps_keys = self.keymap[event.code]
                    except KeyError:
                        continue

                    axis = self.device.absinfo(event.code)

                    for ps_key in ps_keys:
                        negate = ps_key.startswith('-')
                        self.state[ps_key.lstrip('-')] = clamp(event.value / axis.max, -1, 1) * (-1 if negate else 1)

        except (asyncio.CancelledError, OSError) as e:
            print_verbose("Input events task ended")

    def handler(self, *args):
        if len(args) >= 2 and args[0] == 'org.freedesktop.UPower.Device' and 'Percentage' in args[1]:
            self.battery = args[1]['Percentage']
            print_verbose("Found dbus interface for battery level reading. Value: " + str(self.battery))

    def get_battery_dbus_interface(self):
        dbus_loop = DBusGMainLoop()
        bus = dbus.SystemBus(mainloop=dbus_loop)
        upower = bus.get_object('org.freedesktop.UPower', '/org/freedesktop/UPower')

        upower_list = upower.get_dbus_method('EnumerateDevices', 'org.freedesktop.UPower')()

        for device in upower_list:
            dev = bus.get_object('org.freedesktop.UPower', device)
            bus.add_signal_receiver(self.handler, signal_name='PropertiesChanged', bus_name=dev.bus_name,
                                    path=dev.object_path)

            dbus_interface = dbus.Interface(dev, 'org.freedesktop.UPower.Device')

            dbus_properties_interface = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
            properties = dbus_properties_interface.GetAll("org.freedesktop.UPower.Device")

            if properties["Serial"] == self.serial:
                self.battery = properties["Percentage"]
                print_verbose("Found dbus interface for battery level reading. Value: " + str(self.battery))
                return dbus_interface, dbus_properties_interface

        return None, None

    @property
    def connected(self):
        try:
            return self._loop.is_running()
        except AttributeError:
            return self.thread.is_alive()

    @property
    def battery_status(self):
        if self.battery is None:
            return 0x00
        elif self.battery < 10:
            return 0x01
        elif self.battery < 25:
            return 0x02
        elif self.battery < 75:
            return 0x03
        elif self.battery < 90:
            return 0x04
        else:
            return 0x05

    @property
    def report(self):
        state = self.state.copy()
        state["timestamp"] = time.time_ns() // 1000
        return state


class UDPServer:
    MAX_PADS = 4
    TIMEOUT = 5

    def __init__(self, host='', port=26760):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        print_verbose("Started UDP server with ip " + str(host) + ", port " + str(port))
        self.counter = 0
        self.clients = dict()
        self.slots = [None] * UDPServer.MAX_PADS
        self.stop_event = threading.Event()

    def _res_ports(self, index):
        device = self.slots[index]
        if device is None:
            device = index

        return Message('ports', device)

    def _req_ports(self, message, address):
        requests_count = struct.unpack("<I", message[20:24])[0]

        for slot_id in message[24: 24 + requests_count]:
            try:
                self.sock.sendto(self._res_ports(slot_id), address)
            except IndexError:
                print('[udp] Received malformed ports request')
                return

    def _req_data(self, message, address):
        reg_id = message[20]
        slot_id = message[21]
        # reg_mac = message[22:28]

        if address not in self.clients:
            print(F'[udp] Client connected: {address[0]}:{address[1]}')
            self.clients[address] = dict(controllers=[False] * 4)

        self.clients[address]["timestamp"] = time.time()

        # Register all slots
        if reg_id == 0:
            self.clients[address]["controllers"] = [True] * 4

        # Register a single slot
        elif reg_id == 1:
            self.clients[address]["controllers"][slot_id] = True

        # MAC-based registration (TODO)
        elif reg_id == 2:
            print("[udp] Ignored request for MAC-based registration (unimplemented)")
        else:
            print(F"[udp] Unknown data request type: {reg_id}")

    def _res_data(self, controller_index, message):
        now = time.time()
        for address, data in self.clients.copy().items():
            if now - data["timestamp"] > UDPServer.TIMEOUT:
                print(F'[udp] Client disconnected: {address[0]}:{address[1]}')
                del self.clients[address]

            elif data["controllers"][controller_index]:
                self.sock.sendto(message, address)

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
        device_state = device.report

        # Acceleration in g's
        sensors = [
            device_state.get('accel_y'),
            - device_state.get('accel_z'),
            device_state.get('accel_x'),
        ]

        # Gyro rotation in deg/s
        if report_motion:
            sensors.extend([
                - device_state.get('motion_y'),
                - device_state.get('motion_z'),
                device_state.get('motion_x')
            ])
        else:
            sensors.extend([0.0, 0.0, 0.0])

        buttons1 = 0x00
        buttons1 |= int(abs_to_button(device_state.get("button_share", 0x00)) / 255)
        buttons1 |= int(abs_to_button(device_state.get("button_l3", 0x00)) / 255) << 1
        buttons1 |= int(abs_to_button(device_state.get("button_r3", 0x00)) / 255) << 2
        buttons1 |= int(abs_to_button(device_state.get("button_options", 0x00)) / 255) << 3
        buttons1 |= int(abs_to_button(device_state.get("dpad_up", 0x00)) / 255) << 4
        buttons1 |= int(abs_to_button(device_state.get("dpad_right", 0x00)) / 255) << 5
        buttons1 |= int(abs_to_button(device_state.get("dpad_down", 0x00)) / 255) << 6
        buttons1 |= int(abs_to_button(device_state.get("dpad_left", 0x00)) / 255) << 7

        buttons2 = 0x00
        buttons2 |= int(abs_to_button(device_state.get("button_l2", 0x00)) / 255)
        buttons2 |= int(abs_to_button(device_state.get("button_r2", 0x00)) / 255) << 1
        buttons2 |= int(abs_to_button(device_state.get("button_l1", 0x00)) / 255) << 2
        buttons2 |= int(abs_to_button(device_state.get("button_r1", 0x00)) / 255) << 3
        buttons2 |= int(abs_to_button(device_state.get("button_triangle", 0x00)) / 255) << 4
        buttons2 |= int(abs_to_button(device_state.get("button_circle", 0x00)) / 255) << 5
        buttons2 |= int(abs_to_button(device_state.get("button_cross", 0x00)) / 255) << 6
        buttons2 |= int(abs_to_button(device_state.get("button_square", 0x00)) / 255) << 7

        data = [
            0x01,  # is active (true)
            *struct.pack('<I', self.counter),
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

            *struct.pack('<Q', device_state.get("timestamp")),  # Motion data timestamp
            *struct.pack('<ffffff', *sensors)  # Accelerometer and Gyroscope data
        ]

        self.counter += 1

        self._res_data(device.index, Message('data', device, data))

    def report_clean(self, device):
        self._res_data(device.index, Message('data', device))

    def add_device(self, device, motion_device, motion_only=False):
        # Find an empty slot for the new device
        for i, slot in enumerate(self.slots):
            if not slot:
                self.slots[i] = SwitchDevice(self, i, device, motion_device, motion_only)
                return i

        # All four slots have been allocated
        return UDPServer.MAX_PADS

    def add_devices(self, device, motion_devices, motion_only=False):
        i = -1

        # Any motion device except the first one should have only motion reading task to avoid 'device busy' errors
        for i, motion_device in enumerate(motion_devices):
            if self.add_device(device, motion_device, motion_only if i == 0 else True) == UDPServer.MAX_PADS:
                return i

        return i + 1

    def print_slots(self):
        print(colored(F" {self.sock.getsockname()} ".center(55, "="), attrs=["bold"]))

        print(colored("{:<14} {:<12} {:<12} {:<12}", attrs=["bold"])
              .format("Device", "LED status", "Battery Lv", "MAC Addr"))

        for i, slot in enumerate(self.slots):
            if not slot:
                print(str(i + 1) + " ❎ ")
            else:
                device = str(i + 1) + " "
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

                if slot.battery:
                    battery = F"{str(slot.battery)} {chr(ord('▁') + int(slot.battery * 7 / 100))}"
                else:
                    battery = "❌"

                mac = slot.serial

                # print device after calculating alignment because the gamepad symbols cause alignment errors
                print(
                    F'{"":<14} {colored(F"{leds:<12}", "green")} {colored(F"{battery:<12}", "green")} {mac:<12}\r{device}')

        print(colored("".center(55, "="), attrs=["bold"]))

    def connected_devices(self):
        return sum(d is not None for d in self.slots)

    def _worker(self):
        while not self.stop_event.is_set():
            self._handle_request(self.sock.recvfrom(1024))
        self.sock.close()

    def start(self):
        self.thread = threading.Thread(target=self._worker)
        self.thread.start()

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


def handle_devices(stop_event):
    def add_server(ip, port):
        try:
            servers.append(UDPServer(ip, port))
        except (OSError, PermissionError) as e:
            return False
        else:
            servers[-1].start()
            return True

    def add_devices(device, motion_devices, motion_only=False):
        while motion_devices:
            for server in servers:
                added = server.add_devices(device, motion_devices, motion_only)
                motion_devices = motion_devices[added:]

                # All devices added
                if not motion_devices:
                    break

                # Some devices were added, the rest must be added with motion_only set
                elif added:
                    motion_only = True

            # All servers are full
            if motion_devices:
                offset = 0
                while not add_server(args.ip, args.port + offset):
                    offset += 1

    def print_slots():
        for server in servers:
            server.print_slots()

        print()

    blacklisted = []
    servers = []

    print("Looking for Nintendo Switch controllers...")

    taken_slots = lambda: sum(server.connected_devices() for server in servers)

    while not stop_event.is_set():
        slots = sum((server.slots for server in servers), [])

        # Filter devices that were already assigned or couldn't be assigned
        evdev_devices = [evdev.InputDevice(path) for path in evdev.list_devices()
                         if path not in blacklisted
                         and not any(path in [slot.device.path, slot.motion_device.path] for slot in slots if slot)]

        valid_device_names = ["Nintendo Switch Left Joy-Con",
                              "Nintendo Switch Right Joy-Con",
                              "Nintendo Switch Pro Controller",
                              "Nintendo Switch Virtual Pro Controller",
                              "Nintendo Switch Combined Joy-Cons"]

        # Filter for Nintendo Switch devices only
        evdev_devices = [d for d in evdev_devices if
                         any(d.name.startswith(valid_name) for valid_name in valid_device_names)]

        # Added for backwards compatibility with older versions of joycond
        combined_devices = [d for d in evdev_devices if d.name == "Nintendo Switch Combined Joy-Cons"]

        # players 1-4, 0 for invalid devices
        players = {i: [] for i in range(UDPServer.MAX_PADS + 1)}
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
                blacklisted.extend([device.path for device in devices])
                continue

            # Could happen when one Joy-Con in a combined pair is disconnected and then reconnected
            previously_assigned = next(
                (slot for slot in slots if slot and player == slot.player_id and "Combined" in slot.name), None)
            if previously_assigned:
                add_devices(previously_assigned.device, devices, not previously_assigned.motion_only)
                continue

            # Physical device (Pro-Con or a single Joy-Con)
            if all(d.uniq == devices[0].uniq for d in devices):
                devices.sort(key=lambda d: d.name in valid_device_names, reverse=True)
                try:
                    device = next(d for d in devices if d.name in valid_device_names)

                # Device is not yet 'paired'
                except StopIteration:
                    continue

                devices.remove(device)
                motion_devices = devices

            # Virtual device (Combined Joy-Cons or Virtual Pro-Con)
            else:
                try:
                    device = next(d for d in devices if "Combined" in d.name or "Virtual" in d.name)
                    devices.remove(device)

                # Added for compatibility with older versions of joycond
                except StopIteration:
                    # Devices are not yet 'paired'
                    if not combined_devices:
                        continue

                    # Sort combined devices by creation time.
                    # This is the best guess we have to match combined device to it's individual Joy-Cons
                    if len(combined_devices) > 1:
                        context = pyudev.Context()
                        combined_devices.sort(key=lambda d: next(
                            iter(context.list_devices(sys_name=os.path.basename(d.path)))).time_since_initialized,
                                              reverse=True)

                    device = combined_devices.pop(0)

                # Right Joy-Con is mapped first
                motion_devices = sorted(devices, key=lambda d: "Right" in d.name, reverse=True)

                if args.right_only or args.left_only:
                    removed = "Right" if args.left_only else "Left"
                    removed_device = next((d for d in motion_devices if removed in d.name), None)
                    if removed_device:
                        motion_devices.remove(removed_device)
                        blacklisted.append(removed_device.path)

            add_devices(device, motion_devices)

        if active_devices != taken_slots():
            print_slots()
            active_devices = taken_slots()

        # Detect disconnected devices
        for server in servers:
            for i, slot in enumerate(server.slots):
                if slot and not slot.connected:
                    server.slots[i] = None

        if active_devices != taken_slots():
            print_slots()

        loop.run()
        stop_event.wait(0.5)  # sleep for 0.5 seconds to avoid 100% cpu usage

    for server in servers:
        server.stop()


parser = argparse.ArgumentParser()
parser.add_argument("-v", "--verbose", help="show debug messages", action="store_true")
parser.add_argument("-ip", "--ip", help="set custom port, default is 127.0.0.1", default="127.0.0.1")
parser.add_argument("-p", "--port", help="set custom port, default is 26760", type=int, default=26760)

select_motion = parser.add_mutually_exclusive_group()
select_motion.add_argument("-l", "--left-only", help="use only left Joy-Cons for combined device motion",
                           action="store_true")
select_motion.add_argument("-r", "--right-only", help="use only right Joy-Cons for combined device motion",
                           action="store_true")

args = parser.parse_args()


def main():
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

    stop_event = threading.Event()

    def signal_handler(signal, frame):
        print("Stopping servers...")
        stop_event.set()
        loop.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    handle_devices(stop_event)


if __name__ == "__main__":
    main()
