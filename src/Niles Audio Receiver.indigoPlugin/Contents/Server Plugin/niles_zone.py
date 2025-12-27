#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
Niles Audio Zone Device Class

Represents a single audio zone on a Niles Audio receiver.
Zone devices maintain their own state but all commands are sent through the receiver.

Zones are child devices that depend on a parent receiver device for communication.
"""

# region Python Imports
from typing import TYPE_CHECKING

import indigo

if TYPE_CHECKING:
    from plugin import Plugin
# endregion


class NilesZone:
    """
    Represents a single zone on a Niles Audio receiver.
    
    The zone is a child device that delegates all communication to its
    parent receiver device. It maintains state information that is
    updated when the receiver parses status responses.
    
    Zone states include:
    - Power on/off
    - Volume (0-100)
    - Source (1-6)
    - Mute status
    - Bass level
    - Treble level
    """

    def __init__(self, plugin: 'Plugin', device: indigo.Device):
        """
        Initialize the Niles zone.
        
        Args:
            plugin: Reference to the main plugin instance
            device: The Indigo device this zone represents
        """
        self.host_plugin = plugin
        self.device_id = device.id  # Store ID, not device object (can become stale)
        self.logger = plugin.logger
        
        # Zone configuration from device properties
        self.zone_number = int(device.pluginProps.get('zoneNumber', '1'))
        self.receiver_id = int(device.pluginProps.get('sourceReceiver', '0'))
        
        self.logger.debug(f"NilesZone initialized: {device.name} (Zone {self.zone_number})")

    @property
    def device(self) -> indigo.Device:
        """Get a fresh device reference from Indigo."""
        return indigo.devices[self.device_id]

    @property
    def is_powered_on(self) -> bool:
        """Get the current power state."""
        return self.device.states.get("isPoweredOn", False)

    @property
    def volume(self) -> int:
        """Get the current volume level (0-100)."""
        return self.device.states.get("volume", 0)

    @property
    def source(self) -> int:
        """Get the current source number (1-6)."""
        return self.device.states.get("source", 1)

    @property
    def is_muted(self) -> bool:
        """Get the current mute state."""
        return self.device.states.get("isMuted", False)

    @property
    def bass_level(self) -> int:
        """Get the current bass level."""
        return self.device.states.get("bassLevel", 0)

    @property
    def treble_level(self) -> int:
        """Get the current treble level."""
        return self.device.states.get("trebleLevel", 0)

    def get_source_options(self) -> list:
        """
        Get available source options for this zone.
        
        Retrieves source labels from the parent receiver's configuration.
        
        Returns:
            List of (source_number, source_label) tuples
        """
        source_options = []
        
        try:
            # Get the parent receiver device
            receiver_dev = indigo.devices.get(self.receiver_id)
            if not receiver_dev:
                return source_options
            
            # Build source list from receiver properties
            for x in range(1, 7):
                source_prop_name = f"source{x}Label"
                source_label = receiver_dev.pluginProps.get(source_prop_name, "")
                if source_label:
                    source_options.append((str(x), f"Source {x}: {source_label}"))
                    
        except Exception as e:
            self.logger.error(f"Error getting source options: {e}")
        
        return source_options
