#!/usr/bin/python
# -*- coding:utf-8 -*-
from bottle import get,post,run,route,request,template,static_file
import threading
import os
import socket
import time
import atexit
import signal
from TurretHandler import TurretHandler


turret_handler = TurretHandler()

#register for abnormal exit types
atexit.register(turret_handler.gracefulExit)
signal.signal(signal.SIGTERM, turret_handler.gracefulExit)
signal.signal(signal.SIGINT, turret_handler.gracefulExit)

#Set the Horizontal vertical servo parameters
last_servo_pulses = turret_handler.getLastServoPulses()
print("last servo pulse values [H, V]", last_servo_pulses)
HPulse = last_servo_pulses[0]  # Default is ~925  Sets the initial Pulse
HStep = 0                      # Sets the initial step length
VPulse = last_servo_pulses[1]  # Default is ~2365 Sets the initial Pulse
VStep = 0                      # Sets the initial step length

start = int(time.time())


@get("/")
def index():
    return template("index")
    
@route('/<filename>')
def server_static(filename):
    return static_file(filename, root='./')

@route('/fonts/<filename>')
def server_fonts(filename):
    return static_file(filename, root='./fonts/')
  
@get("/api/turret-state")
def turretState():
    is_enabled = turret_handler.getTurretState()
    state = 'Enabled' if (is_enabled) else 'Disabled'
    return {'state': state}
    

    
@post("/api/cmd")
def cmd():
    global turret_handler
    code = request.body.read().decode()
    print ("code ", code)

    if code == "enable_turret":
        turret_handler.setTurretState(True)
        print("turret auto tracking enabled")
    elif code == "disable_turret":
        turret_handler.setTurretState(False)
        print("turret auto tracking disabled")
    elif code == "enable_aux_laser":
        turret_handler.setAuxLaserOn()
        print("Aux laser ON")
    elif code == "disable_aux_laser":
        turret_handler.setAuxLaserOff()
        print("Aux laser OFF")
  
    return "OK"



@post("/api/control-cmd")
def controlCmd():
    global turret_handler
    global HStep,VStep
    code = request.body.read().decode()
    print ("code ",code)
   
    if code == "stop":
        HStep = 0
        VStep = 0
        print("stop")
    elif code == "up":
        VStep = -15
        print("up")
    elif code == "down":
        VStep = 15
        print("down")
    elif code == "left":
        HStep = 15
        print("left")
    elif code == "right":
        HStep = -15
        print("right")
    return "OK"
    
    

def streamingFunc():
    base_path = 'mjpg-streamer/mjpg-streamer-experimental/'
    os.system('./' + base_path + 'mjpg_streamer -i "./' + base_path + 'input_opencv.so" -o "./' + base_path + 'output_http.so -w ./' + base_path + 'www"') 
    # ./mjpg_streamer -i "./input_uvc.so" -o "./output_http.so -w ./www"


def turretFunc():
    global turret_handler
    turret_handler.startDetectionCycle()



def turretControlfunc():
    global HPulse,VPulse,HStep,VStep,turret_handler,start
    
    last_servo_pulses = turret_handler.getLastServoPulses()
    HPulse = last_servo_pulses[0] 
    VPulse = last_servo_pulses[1]
    is_turret_enabled = turret_handler.getTurretState()
    
    if(is_turret_enabled == True):
        # skip to avoid clashing with automatic detection movements
        HStep = 0
        VStep = 0
        time.sleep(5)
        
    else:
        if(HStep != 0):
            VStep = 0
            HPulse += HStep
            if(HPulse > 1100):  # 57 deg - LEFT
                HPulse = 1100
            if(HPulse < 725):   # 17 deg - RIGHT
                HPulse = 725
                
            #set channel 1, the Horizontal servo
            print("H", HPulse)
            turret_handler.rotateGraduallyByPulse(1,HPulse)  
            start = int(time.time())        


        if(VStep != 0):
            HStep = 0
            VPulse -= VStep
            if(VPulse > 2085):  # 142 deg UP
                VPulse = 2085
            if(VPulse < 2020):  # 138 deg DOWN
                VPulse = 2020
                
            #set channel 0, the vertical servo
            print("V", VPulse)
            turret_handler.rotateGraduallyByPulse(0, VPulse)   
            start = int(time.time())
            
        end = int(time.time())
        if((end - start) > 5): #3
            HStep = 0
            VStep = 0
            start = int(time.time())

    
    global t
    t = threading.Timer(0.05, turretControlfunc)
    t.start()

 
    
try:
   
    streamingThread = threading.Thread(target = streamingFunc)
    streamingThread.setDaemon(True)
    streamingThread.start()
    
    turretThread = threading.Timer(0.02, turretFunc)
    turretThread.setDaemon(True)
    turretThread.start()
    
    controlThread = threading.Timer(1, turretControlfunc)
    controlThread.setDaemon(True)
    controlThread.start()


    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))
    localhost = s.getsockname()[0]

    run(host=localhost, port="8001")
except:
    print ("\nProgram end")
    turret_handler.gracefulExit()
    exit()
