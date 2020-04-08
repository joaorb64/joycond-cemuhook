======
joycond-cemuhook
======

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu...

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`'s implementation.

Currently supports one single joycon L+R combination. It detects and uses only the Right joycon's motion as to simulate a Wii Remote.

How to use
--------
- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>` (or wait for a Kernel release with hid-nintendo)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>` userspace driver
- Connect left and right joycons and press L+R to connect them as merged joycons
- Run joycond-cemuhook: ``python3 joycond-cemuhook``
- Open a compatible emulator and enable cemuhook UDP motion input
