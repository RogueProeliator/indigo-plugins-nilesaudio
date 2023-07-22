#! /usr/bin/env python
# -*- coding: utf-8 -*-
#######################################################################################
# Niles Audio Receiver Plugin by RogueProeliator <rp@rogueproeliator.com>
# Indigo plugin designed to allow full control of a Niles Audio receiver such as the
# ZR-4 and ZR-6
#
# Command structure based on Niles Audio's published specification found in the ZR-6
# instruction manual
#######################################################################################

# region Python imports
import nilesAudioDevices

from RPFramework.RPFrameworkPlugin import RPFrameworkPlugin
# endregion


class Plugin(RPFrameworkPlugin):
	
	#######################################################################################
	# region Class construction and destruction methods
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class creation; set up the device tracking
	# variables for later use
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
		# RP framework base class's init method
		super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs, managed_device_class_module=nilesAudioDevices)

	# endregion
	#######################################################################################

	#######################################################################################
	# region Actions object callback handlers/routines
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called from the user executing the menu item action to send
	# an arbitrary command code to the Onkyo receiver
	# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def send_arbitrary_command(self, valuesDict, typeId):
		try:
			device_id    = valuesDict.get("targetDevice", "0")
			command_code = valuesDict.get("commandToSend", "").strip()
		
			if device_id == "" or device_id == "0":
				# no device was selected
				error_dict = indigo.Dict()
				error_dict["targetDevice"] = "Please select a device"
				return False, valuesDict, error_dict
			elif command_code == "":
				error_dict = indigo.Dict()
				error_dict["commandToSend"] = "Enter command to send"
				return False, valuesDict, error_dict
			else:
				# send the code using the normal action processing...
				action_params = indigo.Dict()
				action_params["commandCode"] = command_code
				self.execute_action(pluginAction=None, indigoActionId="SendArbitraryCommand", indigoDeviceId=int(device_id), paramValues=action_params)
				return True, valuesDict
		except:
			self.logger.exception("Failed to send command to device")
			return False, valuesDict

	# endregion
	#######################################################################################
