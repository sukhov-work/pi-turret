import cv2
import cv2.dnn
import numpy as np
from picamera2 import Picamera2
from libcamera import controls
import time
import RPi.GPIO as GPIO
from PCA9685 import PCA9685
import atexit
import signal
from gpiozero import LED
import RPi.GPIO as GPIO
import syslog
import os

from ultralytics import YOLO
from ultralytics.utils import ASSETS, yaml_load
from ultralytics.utils.checks import check_yaml
from YOLOv8 import YOLOv8
from YOLOv8 import DetectionResult
import traceback



"""
Example usage 
h = TurretHandler()
h.setTurretState(is_enabled = True)
h.startDetectionCycle()
"""
class TurretHandler:

    def __init__(self):
        self.is_turret_enabled = False
        self.servo_state_file = 'last_servo_state.txt'
        
        self.aux_laser_led = LED(27)
        self.main_laser_led = LED(26)
        self.status_led = LED(23)
        
        self.camera_width = 1152
        self.camera_height = 1152
        
        print ("Initializing PCA9685...")
        self.pwm = PCA9685(debug=True)
        self.pwm.setPWMFreq(50)

        self.base_x_angle = 44  # Pulse eq 990
        self.base_y_angle = 138 # Pulse eq 2040
        
        last_servo_state = self.loadLastServoState()
        if (len(last_servo_state) == 0):
            self.angle_x = self.base_x_angle 
            self.angle_y = self.base_y_angle
            self.persistServoState()
        else: 
            self.angle_x = last_servo_state[0]
            self.angle_y = last_servo_state[1]
            print("loaded servo state from file, angles: ", self.angle_x ,self.angle_y)
                

        self.pwm.setRotationAngle(1, self.base_x_angle)
        time.sleep(0.2)
        self.pwm.setRotationAngle(0, self.base_y_angle)
        time.sleep(0.2)
        
        print('Initializing Yolov8 onnx classification and detection custom model')

        # custom [crow, pigeon, magpie] classes
        self.class_names = yaml_load(check_yaml('models/v8_pigeon_best.yaml'))['names']
        self.rng = np.random.default_rng(3)
        self.colors = self.rng.integers(0, 255, size=(len(self.class_names), 3))

        self.yolov8_detector = YOLOv8('models/v8_pigeon_best_384_int.onnx', '/home/jayson/opencv_test_detection/YoloRunner/models/ssd/', conf_thres=0.7, iou_thres=0.5)

    def loadLastServoState(self):
        if (os.path.isfile(self.servo_state_file) and os.path.getsize(self.servo_state_file) > 0):
            with open(self.servo_state_file, 'r') as f: 
                x, y = [int(x) for x in next(f).split()]
                return [x,y]
        return []
    
    def persistServoState(self):
         with open(self.servo_state_file, 'w') as f: 
             f.write(str(self.angle_x) + " " + str(self.angle_y))
    
    def getLastServoPulses(self):
        hPulse = self.angle_x * (2000 / 180) + 501
        vPulse = self.angle_y * (2000 / 180) + 501
        return [int(hPulse), int(vPulse)]
    
    def setTurretState(self, is_enabled = False):
        self.is_turret_enabled = is_enabled
        self.rotateGradually(self.base_x_angle, self.base_y_angle, self.angle_x, self.angle_y)
          
    def getTurretState(self):
        return self.is_turret_enabled
        
    def getPwm(self):
        return self.pwm
    
    def setAuxLaserOn(self):
        self.aux_laser_led.on()
        time.sleep(0.3)
        return 'ok'
        
    def setAuxLaserOff(self):
        self.aux_laser_led.off()
        time.sleep(0.3)
        return 'ok'
        
    def triggerAuxLaser(self, active_time = 2.0):
        self.aux_laser_led.on()
        time.sleep(active_time)
        self.aux_laser_led.off()
        time.sleep(0.5)
        
    def triggerMainLaser(self, active_time = 2.0):
        self.main_laser_led.on()
        time.sleep(active_time)
        self.main_laser_led.off()
        time.sleep(0.5)
        
   
    def rotateGradually(self, x, y, prev_x, prev_y):
        self.angle_x = x
        self.angle_y = y
        self.pwm.start_PCA9685()
        self.pwm.setRotationAngleGradually(1, x, prev_x)
        self.pwm.setRotationAngleGradually(0, y, prev_y)
        time.sleep(0.2)
        self.pwm.exit_PCA9685()
        self.persistServoState()
    
    def rotateGraduallyByPulse(self, channel, pulse):
        target_angle = int((pulse - 501) / (2000 / 180))
        
        prev_angle = target_angle
        # very limited Y range
        if (channel == 0 and target_angle < (self.base_y_angle + 5) and target_angle > (self.base_y_angle)): 
            prev_angle = self.angle_y
            self.angle_y = target_angle
        elif (channel == 1 and target_angle < (self.base_x_angle + 12) and target_angle > (self.base_x_angle - 15)):
            prev_angle = self.angle_x
            self.angle_x = target_angle
        else:
            print("not supported params", channel, pulse)
            return
            
        self.pwm.start_PCA9685()
        print(channel, pulse, target_angle, prev_angle)
        self.pwm.setServoPulse(channel, pulse)
        
        time.sleep(0.2)
        # self.pwm.exit_PCA9685()
        self.persistServoState()
        time.sleep(0.1)


    def gracefulExit(self, *args):
        self.rotateGradually(self.base_x_angle, self.base_y_angle, self.angle_x, self.angle_y)
        time.sleep(0.5)
        self.pwm.exit_PCA9685()
        time.sleep(0.5)
        self.status_led.off()
        time.sleep(0.1)
        self.aux_laser_led.off()
        time.sleep(0.1)
        self.main_laser_led.off()
        time.sleep(0.5)
        cv2.destroyAllWindows()
        print ("\nProgram end atexit/signal handler")
        syslog.syslog(syslog.LOG_INFO, "Program end atexit/signal handler")


    #tries to infer targets from frame, computes bounding boxes, classes, and confidence scores
    def getObjects(self, frame, targets=[]):
        boxes, scores, class_ids = self.yolov8_detector(frame)
        
        if len(targets) == 0: targets = self.class_names

        detections =[]
        if len(class_ids) != 0:
            for box, score, classId in zip(boxes, scores, class_ids):
                class_name = self.class_names[classId]
                if class_name in targets: 
                    detections.append(DetectionResult(box, score, class_name))
                    
        return detections

    def getTargetPigeonCandidateBox(self, detection_threshold, detection_results):
        target_box = []
        if len(detection_results) != 0:
            is_crow_present_in_frame = any(map(lambda dr: dr.class_name == 'crow' or dr.class_name == 'magpie' , detection_results))
            if (is_crow_present_in_frame):
                print("crow detected, skipping")
                return target_box
            
            #sort to get first highest score pigeon result ( if multitarget)
            detection_results.sort(key=lambda dr: dr.score, reverse=True)
            print(detection_results[0].score, detection_results[0].class_name)
            if (detection_results[0].score > detection_threshold):
                target_box = detection_results[0].box
        return target_box


    #translates provided linear xy coordinates and rotates 2dof platform accordingly. Activates LEDs
    def pointAndFire(self, box):
        prev_angle_x = self.angle_x
        prev_angle_y = self.angle_y
                        
        # offsets from image center adjusted for current resolution
        # '15' divider, can be tuned for precision to scale movement
        # same for y axis, must be negative for correct orientation
        # ((x0 + x1)/2 + half_width) / 15
        rotation_coefficient = 15
        
        calculated_angle_x = - int((int((box[0] + box[2])/2) - self.camera_width / 2 )/ rotation_coefficient) + self.base_x_angle - 1
        calculated_angle_y = - int((int((box[1] + box[3])/2) - self.camera_height / 2 )/ rotation_coefficient) + self.base_y_angle -5
        
        print("calculated rotation angles", calculated_angle_x, calculated_angle_y)
        if (calculated_angle_x > (self.base_x_angle + 12) or calculated_angle_x < (self.base_x_angle - 15) or
         calculated_angle_y > (self.base_y_angle + 5) or calculated_angle_y < (self.base_y_angle)):
             print("calculated angle out of bounds [28 - 56, 138 - 142], skipping", calculated_angle_x, calculated_angle_y)
             return
       
        self.rotateGradually(calculated_angle_x, calculated_angle_y, prev_angle_x, prev_angle_y)
        self.triggerAuxLaser()
        self.triggerMainLaser()
    
    
    #check for humans in the frame to avoid firing
    def isPersonInCurrentFrame(self, frame, person_class_name, person_class_detection_threshold):
       
        classification_results = self.yolov8_detector.getClassificationResults(frame)
        is_person_detected = False
        for cls_res in classification_results:
            if (cls_res.class_name == person_class_name and
                cls_res.score > person_class_detection_threshold):

                print(cls_res.class_name, cls_res.score)
                is_person_detected = True
                break
        return is_person_detected


    ########################
    ##### main routine #####
    ########################
    def startDetectionCycle(self):
        syslog.syslog(syslog.LOG_INFO, "<TurretHandler> main detection cycle start")
   
        # init camera - square resolution multiple of 384x384 is preferable - input size of current trained net (v8 converted onnx)
        picam2 = Picamera2()
        picam2.configure(picam2.create_preview_configuration(main={"format": 'RGB888', "size": (self.camera_width, self.camera_height)}))
        picam2.start()
        picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        
        #if need to calibrate target center
        self.aux_laser_led.on()

        # can fire only if currect detections count > confidende threshold
        detection_confidende_counter = 0
        detection_confidende_threshold = 3
        
        # reset detection counter after # of iterations to avoid too many false positives
        detection_reset_counter = 0
        detection_reset_threshold = 30
        
        # person detection safeguard stuff
        person_class_name = 'person'
        person_class_detection_threshold = 0.9 # should not be higher than 0.3 (30% confidence)  for safety!
        
        try: 
            #Below is the never ending loop that determines what will happen when an object is identified.    
            while True:
                if (not self.is_turret_enabled):
                    detection_confidende_counter = 0
                    self.status_led.off()
                    time.sleep(5)
                    continue
                
                self.status_led.on()
                detection_reset_counter = detection_reset_counter + 1
                # check if we are out of attempts to detect something withing desired range
                # e.g 30 consecutive frames and need to reset all counters to avoid premature detections
                if (detection_confidende_counter < detection_confidende_threshold and
                    detection_reset_counter > detection_reset_threshold):
                    print("detection timeout - reset counter")
                    detection_confidende_counter = 0
                    detection_reset_counter = 0
                    #reset turret position
                    #self.rotateGradually(self.base_x_angle, self.base_y_angle, self.angle_x, self.angle_y)
                    
                frame = picam2.capture_array()
                detetion_results = self.getObjects(frame, targets=['pigeon', 'crow', 'magpie'])
                
                #output image for targeting / debugging purposes
                #combined_img = self.yolov8_detector.draw_detections(frame, self.class_names, self.colors)
                #cv2.imshow("Detected Objects", combined_img)
            
                target_box = self.getTargetPigeonCandidateBox(0.7, detetion_results)
                
                if len(target_box) != 0:         
                    if (target_box[0] < 10 or target_box[2] > (self.camera_width - 10) or
                        target_box[1] < 10 or target_box[3] > (self.camera_height - 10)):
                        print("target out of bounds, skipping", target_box)
                        
                    else:
                        detection_confidende_counter = detection_confidende_counter + 1
                        print("detection counter", detection_confidende_counter)
                        if( detection_confidende_counter >= detection_confidende_threshold):
                            
                            is_person_detected = self.isPersonInCurrentFrame(frame, person_class_name, person_class_detection_threshold)
                            if (is_person_detected):
                                print("person detected - reset counter")
                                syslog.syslog(syslog.LOG_INFO, "<Person> detected skipping detection cycle")
                                detection_confidende_counter = 0
                                continue
                            
                            print("<Pigeon> detected, firing", target_box)
                            syslog.syslog(syslog.LOG_INFO, "<Pigeon> detected, firing")
                            #reset counter for next detection
                            detection_confidende_counter = 0
                            self.pointAndFire(target_box)
                   
                       
                        
                self.status_led.off()
                #exit on space press (only with cv2 img mode)
                if cv2.waitKey(1) & 0xFF == ord(' '):
                    break
                
        except Exception as err:
            strace = traceback.format_exc()
            print(err, strace)
            syslog.syslog(syslog.LOG_ERR, f"<Turret Exception> Unexpected {err=}, {strace=}")
            
        finally:
            self.gracefulExit()

"""
h = TurretHandler()
h.setTurretState(is_enabled = True)
h.startDetectionCycle()
"""
