# pi-turret
Raspberry 4 - based AI autonomous laser turret python project

## Description
The goal is to provide ability for basic hardware compatibple with raspberry PI  to serve as a platform for autonomous selected target recognition, tracing and firing using available video cameras to capture frames 
DNN models to detect specified classes of objects in frames, translate detected object coordinates to motion of servo drives (2 DoF platform) and trigger mounted laser diod.

Project consists of following parts: 
  1) Local python server based on Bottle framework that allows remote control and provides turret data and live streaming from USB webcamera
  2) Turret servo driver (PCA9685) and image frame objects neural net detection handler module (TurretHandler.py) .
  3) Pre trained neural network weigts (yolo8, mobilnet-ssd) for specified targets detection (YOLOv8.py)
  4) Streaming server (mjpg-streamer) to handle USB webcamera real-time streaming


### Running
``` python3 main.py ```

This will start Bottle WSGI server that will be exposed locally by current raspberry IP address on port 8001, eg: `http://192.168.68.145:8001/`  or by current host name, eg `http://pi-jayson.local:8001/` 
On the browser page the turret state and live stream through wecam is available. The autodetection is disabled by default and can be enabled via UI button. Servo controls are available only when autodetection is OFF

<img width="697" alt="Screenshot 2023-10-19 at 12 47 36" src="https://github.com/sukhov-work/pi-turret/assets/58325577/bdfc3588-f104-43b9-99ce-b6a46d39eacf">



### Hardware components
![image](https://github.com/sukhov-work/pi-turret/assets/58325577/d1cbdeec-c369-4b96-a6e8-ec957d1aa91a)



### Software Prerequisites 

 * Raspberry Pi OS ( 64 bit)
 * Python 3.9+
 * Open CV >= 4.4 , compiled for Pi OS - https://github.com/opencv
 * Ultralitics runtime - https://github.com/ultralytics/ultralytics
 * ONNX runtime (python) 
 * picamera2 - https://github.com/raspberrypi/picamera2
 * mjpg-streamer (optional, included in this repo with prebuilt opencv module) -  https://github.com/jacksonliam/mjpg-streamer 


### Training flow and data  
Getting started - https://blog.roboflow.com/how-to-train-yolov8-on-a-custom-dataset
ONNX format - https://github.com/ibaiGorordo/ONNX-YOLOv8-Object-Detection

Final Yolo trained model has been converted to ONNX and quantized to gain speed on per frame detection times ( ~ 3x improvement, more CPU friendly ) 

Main custom yolo8 model training flow: https://colab.research.google.com/drive/1j6nrV2YI72Dps6nHEw-J-5dTGIR1PTq1?usp=sharing
Model used in current detection flow is based on above training results in ./models/v8_pigeon_best_384_int.onnx 

Pigeons dataset used for training (pigeon, crow, magpie classes):  https://universe.roboflow.com/jayson-x-an0sg/pigeons-h30dy



