#! /usr/bin/env python
# -*- coding: utf-8 -*-
#/////////////////////////////////////////////////////////////////////////////////////////
# Niles Audio Receiver Plugin by RogueProeliator <rp@rogueproeliator.com>
# 	See plugin.py for more plugin details and information
#/////////////////////////////////////////////////////////////////////////////////////////

#/////////////////////////////////////////////////////////////////////////////////////////
#region Python imports
import re

import indigo
import RPFramework.RPFrameworkTelnetDevice

from RPFramework.RPFrameworkTelnetDevice import RPFrameworkTelnetDevice
from RPFramework.RPFrameworkCommand import RPFrameworkCommand
#endregion
#/////////////////////////////////////////////////////////////////////////////////////////

#/////////////////////////////////////////////////////////////////////////////////////////
#region Constants and configuration variables
CMD_CREATEZONESTATUSUPDATECOMMAND = "createZoneStatusUpdateCommands"
CMD_ACTIVATEZONEFORCOMMAND        = "activateZoneForCommand"
#endregion
#/////////////////////////////////////////////////////////////////////////////////////////


#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioReceiver
#	Handles the communications and status of a Niles audio receiver which is connected
#	via the serial port
#/////////////////////////////////////////////////////////////////////////////////////////
class NilesAudioReceiverDevice(RPFrameworkTelnetDevice):
	
	#/////////////////////////////////////////////////////////////////////////////////////
	# Class construction and destruction methods
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device, connection_type=RPFrameworkTelnetDevice.CONNECTIONTYPE_SERIAL)
		self.active_control_zone = 0

	#/////////////////////////////////////////////////////////////////////////////////////
	# Processing and command functions
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will process the commands that are not processed automatically by the
	# base class; it will be called on a concurrent thread
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def handleUnmanagedCommandInQueue(self, ipConnection, rpCommand):
		if rpCommand.commandName == CMD_CREATEZONESTATUSUPDATECOMMAND:
			# create a set of commands to update the status of a single zone
			update_command_list = [self.createZoneActivateCommand(rpCommand.commandPayload), self.createZoneStatusRequestCommand(rpCommand.commandPayload)]
			self.queueDeviceCommands(update_command_list)
		
		elif rpCommand.commandName == "createAllZonesStatusRequestCommands":
			# create a set of commands to update the status of all zones defined by the
			# plugin (as child devices)
			update_command_list = []
			for zoneNumber in self.childDevices:
				update_command_list.append(self.createZoneActivateCommand(zoneNumber))
				update_command_list.append(self.createZoneStatusRequestCommand(zoneNumber))
			
			# queue up all the commands at once (so they will run back to back)
			self.queueDeviceCommands(update_command_list)
			
		elif rpCommand.commandName == CMD_ACTIVATEZONEFORCOMMAND:
			# this command will immediately activate the requested zone (per the payload)
			# for control if it is not already active
			if self.active_control_zone != int(rpCommand.commandPayload):
				self.hostPlugin.logger.threaddebug(f"Writing activate zone request for zone {rpCommand.commandPayload}")
				write_command = f"znc,4,{rpCommand.commandPayload}\r"
				ipConnection.write(write_command.encode("ascii"))
			else:
				self.hostPlugin.logger.threaddebug(f"Zone {rpCommand.commandPayload} already active, ignoring activate zone command for efficiency")
				
			# ensure that the delay is in place...
			if rpCommand.postCommandPause == 0.0:
				rpCommand.postCommandPause = 0.1
				
		elif rpCommand.commandName == "createAllZonesMuteCommands":
			# this command will be fired whenever the plugin needs to create the commands that will mute
			# all zones (must be done individually)
			mute_command_list = []
			for zoneNumber in self.childDevices:
				if self.childDevices[zoneNumber].indigoDevice.states["isPoweredOn"] == True and self.childDevices[zoneNumber].indigoDevice.states["isMuted"] == False:
					self.hostPlugin.logger.threaddebug(f"Mute All: muting zone {zoneNumber}")
					mute_command_list.append(RPFrameworkCommand(RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, commandPayload=f"zsc,{zoneNumber},11", postCommandPause=0.1))
					mute_command_list.append(RPFrameworkCommand(CMD_CREATEZONESTATUSUPDATECOMMAND, commandPayload=str(zoneNumber), postCommandPause=0.1))
			self.queueDeviceCommands(mute_command_list)
	
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to update
	# the status of a zone defined for this receiver
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def createZoneStatusRequestCommand(self, zoneNumber):
		return RPFrameworkCommand(RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, commandPayload="znc,5", postCommandPause=0.1)
			
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to activate
	# a zone (for control) on this receiver
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def createZoneActivateCommand(self, zoneNumber):	
		return RPFrameworkCommand(CMD_ACTIVATEZONEFORCOMMAND, commandPayload=str(zoneNumber), postCommandPause=0.1)

	#/////////////////////////////////////////////////////////////////////////////////////
	# Custom Response Handlers
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a status
	# request for a particular zone
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def zoneStatusResponseReceived(self, responseObj, rpCommand):
		# the response format is a comma-delimited list with the following values:
		# USC, 2, [ZONE], [SOURCE #], [0|1 - ON/OFF], [VOLUME], [0|1 MUTE], [BASE], [TREB]
		response_parser = re.compile(r'^usc,2,(?P<zone>\d+),(?P<source>\d+),(?P<onOff>0|1),(?P<volume>\d+),(?P<mute>0|1),(?P<base>\d+),(?P<treble>\d+)$', re.I)
		status_obj = response_parser.match(responseObj)
		status_info = status_obj.groupdict()
		
		# device status updates are expensive, so only do the update on statuses that are
		# different than current
		self.hostPlugin.logger.debug(f"Received status update for Zone {status_info['zone']}: {responseObj}")
		zone_device = self.childDevices[status_info["zone"]]

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
			self.hostPlugin.logger.debug("Skipping status update for zone that is off")
			
		# finally update the on/off state...
		if zone_device.indigoDevice.states.get("isPoweredOn", False) != status_is_powered_on or force_ui_value_update:
			zone_device.indigoDevice.updateStateOnServer(key="isPoweredOn", value=status_is_powered_on, uiValue=on_off_ui_value)
			
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a request
	# to change the currently-active zone (for control/update commands)
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def activeControlZoneUpdated(self, responseObj, rpCommand):
		# the response format is a comma-delimited response: rznc,4,[zone]
		response_parser = re.compile(r'^rznc,4,(\d+)\s*$', re.I)
		match_obj = response_parser.match(responseObj)
		self.active_control_zone = int(match_obj.group(1))
		self.hostPlugin.logger.threaddebug(f"Updated active control zone to {match_obj.group(1)}")
				
		
#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioZone
#	Handles the status and representation of a zone associated with a Niles Audio multi-
#	zone receiver
#/////////////////////////////////////////////////////////////////////////////////////////
class NilesAudioZone(RPFramework.RPFrameworkNonCommChildDevice.RPFrameworkNonCommChildDevice):

	#/////////////////////////////////////////////////////////////////////////////////////
	# Class construction and destruction methods
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device)
		
	#/////////////////////////////////////////////////////////////////////////////////////
	# Validation and GUI functions
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine is called to retrieve a dynamic list of elements for an action (or
	# other ConfigUI based) routine
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def getConfigDialogMenuItems(self, filter, valuesDict, typeId, targetId):
		# we need the parent (receiver) device in order to get the list of
		# available sources...
		parent_receiver = self.hostPlugin.managed_devices[int(self.indigoDevice.pluginProps["sourceReceiver"])]
		
		source_options = []
		for x in range(1, 7):
			source_prop_name = f"source{x}Label"
			if parent_receiver.indigoDevice.pluginProps[source_prop_name] != "":
				source_options.append((f"{x}", f"Source {x}: {parent_receiver.indigoDevice.pluginProps[source_prop_name]}"))
			
		return source_options
