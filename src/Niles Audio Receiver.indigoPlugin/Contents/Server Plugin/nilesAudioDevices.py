#! /usr/bin/env python
# -*- coding: utf-8 -*-
#######################################################################################
# Niles Audio Receiver Plugin by RogueProeliator <rp@rogueproeliator.com>
#######################################################################################

# region Python imports
import re

import indigo

from RPFramework.RPFrameworkTelnetDevice import RPFrameworkTelnetDevice
from RPFramework.RPFrameworkNonCommChildDevice import RPFrameworkNonCommChildDevice
from RPFramework.RPFrameworkCommand import RPFrameworkCommand
# endregion

#######################################################################################
# region Constants and configuration variables

CMD_CREATEZONESTATUSUPDATECOMMAND = "createZoneStatusUpdateCommands"
CMD_ACTIVATEZONEFORCOMMAND        = "activateZoneForCommand"

# endregion
#######################################################################################


class NilesAudioReceiverDevice(RPFrameworkTelnetDevice):
	
	#######################################################################################
	# region Class construction and destruction methods
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device, connection_type=RPFrameworkTelnetDevice.CONNECTIONTYPE_SERIAL)
		self.active_control_zone = 0

	# endregion
	#######################################################################################

	#######################################################################################
	# region Processing and command functions
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will process the commands that are not processed automatically by the
	# base class; it will be called on a concurrent thread
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def handle_unmanaged_command_in_queue(self, ip_connection, rp_command):
		if rp_command.commandName == CMD_CREATEZONESTATUSUPDATECOMMAND:
			# create a set of commands to update the status of a single zone
			update_command_list = [self.create_zone_activate_command(rp_command.command_payload), self.create_zone_status_request_command(rp_command.command_payload)]
			self.queue_device_commands(update_command_list)
		
		elif rp_command.command_name == "createAllZonesStatusRequestCommands":
			# create a set of commands to update the status of all zones defined by the
			# plugin (as child devices)
			update_command_list = []
			for zone_number in self.child_devices:
				update_command_list.append(self.create_zone_activate_command(zone_number))
				update_command_list.append(self.create_zone_status_request_command(zone_number))
			
			# queue up all the commands at once (so they will run back to back)
			self.queue_device_commands(update_command_list)
			
		elif rp_command.commandName == CMD_ACTIVATEZONEFORCOMMAND:
			# this command will immediately activate the requested zone (per the payload)
			# for control if it is not already active
			if self.active_control_zone != int(rp_command.command_payload):
				self.host_plugin.logger.threaddebug(f"Writing activate zone request for zone {rp_command.command_payload}")
				write_command = f"znc,4,{rp_command.command_payload}\r"
				ip_connection.write(write_command.encode("ascii"))
			else:
				self.host_plugin.logger.threaddebug(f"Zone {rp_command.command_payload} already active, ignoring activate zone command for efficiency")
				
			# ensure that the delay is in place...
			if rp_command.post_command_pause == 0.0:
				rp_command.post_command_pause = 0.1
				
		elif rp_command.command_name == "createAllZonesMuteCommands":
			# this command will be fired whenever the plugin needs to create the commands that will mute
			# all zones (must be done individually)
			mute_command_list = []
			for zone_number in self.child_devices:
				if self.child_devices[zone_number].indigoDevice.states["isPoweredOn"] and not self.child_devices[zone_number].indigoDevice.states["isMuted"]:
					self.host_plugin.logger.threaddebug(f"Mute All: muting zone {zone_number}")
					mute_command_list.append(RPFrameworkCommand(RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, command_payload=f"zsc,{zone_number},11", post_command_pause=0.1))
					mute_command_list.append(RPFrameworkCommand(CMD_CREATEZONESTATUSUPDATECOMMAND, command_payload=str(zone_number), post_command_pause=0.1))
			self.queue_device_commands(mute_command_list)
	
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to update
	# the status of a zone defined for this receiver
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def create_zone_status_request_command(self, zone_number):
		return RPFrameworkCommand(RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, command_payload="znc,5", post_command_pause=0.1)
			
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to activate
	# a zone (for control) on this receiver
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def create_zone_activate_command(self, zone_number):
		return RPFrameworkCommand(CMD_ACTIVATEZONEFORCOMMAND, command_payload=str(zone_number), post_command_pause=0.1)

	# endregion
	#######################################################################################

	#######################################################################################
	# region Custom Response Handlers
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a status
	# request for a particular zone
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def zone_status_response_received(self, response_obj, rp_command):
		# the response format is a comma-delimited list with the following values:
		# USC, 2, [ZONE], [SOURCE #], [0|1 - ON/OFF], [VOLUME], [0|1 MUTE], [BASE], [TREB]
		response_parser = re.compile(r'^usc,2,(?P<zone>\d+),(?P<source>\d+),(?P<onOff>0|1),(?P<volume>\d+),(?P<mute>0|1),(?P<base>\d+),(?P<treble>\d+)$', re.I)
		status_obj = response_parser.match(response_obj)
		status_info = status_obj.groupdict()
		
		# device status updates are expensive, so only do the update on statuses that are
		# different than current
		self.host_plugin.logger.debug(f"Received status update for Zone {status_info['zone']}: {response_obj}")
		zone_device = self.child_devices[status_info["zone"]]

		# get the on/off status as this will determine what info we update; do not update it now
		# since our uiValue may change depending upon other conditions
		status_is_powered_on = status_info["onOff"] == "1"
		force_ui_value_update = False
		on_off_ui_value = ""

		# we may only update the remainder of the states if the zone is powered on... otherwise
		# the information is not reliable
		if status_is_powered_on:
			zone_states_to_update = []
		
			if zone_device.indigoDevice.states.get("source", "") != status_info["source"]:
				ui_source_value = self.indigoDevice.pluginProps.get(f"source {status_info['source']} Label", "")
				if ui_source_value == "":
					ui_source_value = status_info["source"]
				zone_states_to_update.append({"key": "source", "value": int(status_info["source"]), "uiValue": ui_source_value})
		
			status_volume = int(status_info["volume"])
			if int(zone_device.indigoDevice.states.get("volume", "0")) != status_volume:
				force_ui_value_update = True
				zone_states_to_update.append({"key": "volume", "value": status_volume})
			
			if zone_device.indigoDevice.states.get("isMuted", False) != (status_info["mute"] == "1"):
				force_ui_value_update = True
				zone_states_to_update.append({"key": "isMuted", "value": (status_info["mute"] == "1")})
			
			status_base_level = int(status_info["base"])
			if int(zone_device.indigoDevice.states.get("baseLevel", "0")) != status_base_level:
				zone_states_to_update.append({"key": "baseLevel", "value": status_base_level})
			
			status_treble_level = int(status_info["treble"])
			if int(zone_device.indigoDevice.states.get("trebleLevel", "0")) != status_treble_level:
				zone_states_to_update.append({"key": "trebleLevel", "value": status_treble_level})
				
			# determine the on/off display text
			if status_info["mute"] == "1" or status_volume == 0:
				on_off_ui_value = "muted"
			else:
				on_off_ui_value = str(status_volume)
				
			if len(zone_states_to_update) > 0:
				zone_device.indigoDevice.updateStatesOnServer(zone_states_to_update)
		else:
			on_off_ui_value = "off"
			self.host_plugin.logger.debug("Skipping status update for zone that is off")
			
		# finally update the on/off state...
		if zone_device.indigoDevice.states.get("isPoweredOn", False) != status_is_powered_on or force_ui_value_update:
			zone_device.indigoDevice.updateStateOnServer(key="isPoweredOn", value=status_is_powered_on, uiValue=on_off_ui_value)
			
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a request
	# to change the currently-active zone (for control/update commands)
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def active_control_zone_updated(self, response_obj, rp_command):
		# the response format is a comma-delimited response: rznc,4,[zone]
		response_parser = re.compile(r'^rznc,4,(\d+)\s*$', re.I)
		match_obj = response_parser.match(response_obj)
		self.active_control_zone = int(match_obj.group(1))
		self.host_plugin.logger.threaddebug(f"Updated active control zone to {match_obj.group(1)}")

	# endregion
	#######################################################################################
		

class NilesAudioZone(RPFrameworkNonCommChildDevice):

	#######################################################################################
	# region Class construction and destruction methods
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device)

	# endregion
	#######################################################################################
		
	#######################################################################################
	# region Validation and GUI functions
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine is called to retrieve a dynamic list of elements for an action (or
	# other ConfigUI based) routine
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def getConfigDialogMenuItems(self, filter, valuesDict, typeId, targetId):
		# we need the parent (receiver) device in order to get the list of
		# available sources...
		parent_receiver = self.host_plugin.managed_devices[int(self.indigoDevice.pluginProps["sourceReceiver"])]
		
		source_options = []
		for x in range(1, 7):
			source_prop_name = f"source{x}Label"
			if parent_receiver.indigoDevice.pluginProps[source_prop_name] != "":
				source_options.append((f"{x}", f"Source {x}: {parent_receiver.indigoDevice.pluginProps[source_prop_name]}"))
			
		return source_options

	# endregion
	#######################################################################################
