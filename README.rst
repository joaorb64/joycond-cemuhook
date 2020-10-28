======
joycond-cemuhook
======

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu, etc.

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`_'s implementation.

Supports up to 4 controllers from the following:

- Joycon L+R combination (Select if motion comes from L or R Joycon)
- Switch Pro controller
- Left Joycon
- Right Joycon

How to use
--------

- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
- Clone this repository's files with ``git clone https://github.com/joaorb64/joycond-cemuhook``. The files will be downloaded into a directory ``joycond-cemuhook/``
- Navigate the terminal into the directory with ``cd joycond-cemuhook/``
- Run ``pip3 install -r requirements.txt`` to install extra dependencies
- Run ``python3 joycond-cemuhook.py`` to start joycond-cemuhook

From now on, you'll only need to run ``python3 joycond-cemuhook`` from a terminal on its directory.

- Connect your Nintendo Switch controllers and assign them as intended (using the respective L+R)
- Open a compatible emulator and enable cemuhook UDP motion input

Head to this project's `wiki <https://github.com/joaorb64/joycond-cemuhook/wiki>`_ for detailed instructions on how to configure Cemuhook on emulators.

Media
--------

The Legend of Zelda: Swkyward Sword on Dolphin

.. image:: ../media/zeldass-dolphin.gif


Mario Kart 8 on Cemu (Wine)

.. image:: ../media/mk8-cemu.gif
