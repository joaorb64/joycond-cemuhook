======
joycond-cemuhook
======

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu, etc.

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`_'s implementation.

Currently supports one connected controller from the following:
- Joycon L+R combination. It detects and uses only the Right joycon's motion as to simulate a Wii Remote
- Switch Pro controller

How to use
--------
- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (or wait for a Kernel release with hid-nintendo)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
- Connect left and right joycons and press L+R to connect them as merged joycons
- Run joycond-cemuhook: ``python3 joycond-cemuhook`` (use argument ``wine`` for correct gyro on wine applications such as Cemu)
- Open a compatible emulator and enable cemuhook UDP motion input
