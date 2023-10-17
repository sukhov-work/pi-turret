import time
import cv2
import numpy as np
import onnxruntime
from ultralytics import YOLO

from Utils import xywh2xyxy, draw_detections, multiclass_nms

class DetectionResult:
    def __init__(self, box, score, class_name):
        self.box = box
        self.score = score
        self.class_name = class_name

class YOLOv8:

    def __init__(self, model_path, classification_model_path, conf_thres=0.7, iou_thres=0.5):
        self.conf_threshold = conf_thres
        self.iou_threshold = iou_thres

        # Initialize model
        self.initialize_model(model_path)
        self.initialize_classification_model(classification_model_path)
        

    def __call__(self, image):
        return self.detect_objects(image)

    def initialize_model(self, path):
        self.session = onnxruntime.InferenceSession(path,
                                                    providers=onnxruntime.get_available_providers())
        # Get model info
        self.get_input_details()
        self.get_output_details()

    def initialize_classification_model(self, path):
        classes_file = path + "coco.names"
        with open(classes_file,"rt") as f:
            self.classification_class_names = f.read().rstrip("\n").split("\n")

        weights_path = path + "frozen_inference_graph.pb"
        config_path = path + "ssd_mobilenet_v3_large_coco_2020_01_14.pbtxt"
        
        self.classification_model = cv2.dnn_DetectionModel(weights_path,config_path)
        self.classification_model.setInputSize(384,384)

        self.classification_model.setInputScale(1.0/ 127.5)
        self.classification_model.setInputMean((127.5, 127.5, 127.5))
        self.classification_model.setInputSwapRB(True)


    def getClassificationResults(self, frame):
        start = time.perf_counter()
        class_ids, confidences, bbox = self.classification_model.detect(frame, confThreshold=0.5, nmsThreshold=0.3)
        print(f"Classification (ssd) Inference time: {(time.perf_counter() - start)*1000:.2f} ms")
        cls_detections =[]
        
        if len(class_ids) != 0:
            for class_id, confidence in zip(class_ids.flatten(), confidences.flatten()):
                class_name = self.classification_class_names[class_id - 1]
                cls_detections.append(DetectionResult(None, confidence, class_name))
        
        return cls_detections
    


    def detect_objects(self, image):
        input_tensor = self.prepare_input(image)

        # Perform inference on the image
        outputs = self.inference(input_tensor)

        self.boxes, self.scores, self.class_ids = self.process_output(outputs)

        return self.boxes, self.scores, self.class_ids

    def prepare_input(self, image):
        self.img_height, self.img_width = image.shape[:2]

        input_img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # Resize input image
        input_img = cv2.resize(input_img, (self.input_width, self.input_height))

        # Scale input pixel values to 0 to 1
        input_img = input_img / 255.0
        input_img = input_img.transpose(2, 0, 1)
        input_tensor = input_img[np.newaxis, :, :, :].astype(np.float32)

        return input_tensor


    def inference(self, input_tensor):
        start = time.perf_counter()
        outputs = self.session.run(self.output_names, {self.input_names[0]: input_tensor})

        print(f"Inference time: {(time.perf_counter() - start)*1000:.2f} ms")
        return outputs

    def process_output(self, output):
        predictions = np.squeeze(output[0]).T

        # Filter out object confidence scores below threshold
        scores = np.max(predictions[:, 4:], axis=1)
        predictions = predictions[scores > self.conf_threshold, :]
        scores = scores[scores > self.conf_threshold]

        if len(scores) == 0:
            return [], [], []

        # Get the class with the highest confidence
        class_ids = np.argmax(predictions[:, 4:], axis=1)
        
        # Get bounding boxes for each object
        boxes = self.extract_boxes(predictions)

        # Apply non-maxima suppression to suppress weak, overlapping bounding boxes
        # indices = nms(boxes, scores, self.iou_threshold)
        indices = multiclass_nms(boxes, scores, class_ids, self.iou_threshold)

        return boxes[indices], scores[indices], class_ids[indices]

    def extract_boxes(self, predictions):
        # Extract boxes from predictions
        boxes = predictions[:, :4]

        # Scale boxes to original image dimensions
        boxes = self.rescale_boxes(boxes)

        # Convert boxes to xyxy format
        boxes = xywh2xyxy(boxes)

        return boxes

    def rescale_boxes(self, boxes):

        # Rescale boxes to original image dimensions
        input_shape = np.array([self.input_width, self.input_height, self.input_width, self.input_height])
        boxes = np.divide(boxes, input_shape, dtype=np.float32)
        boxes *= np.array([self.img_width, self.img_height, self.img_width, self.img_height])
        return boxes

    def draw_detections(self, image, class_names, colors, draw_scores=True, mask_alpha=0.4):
        print(self.class_ids)
        return draw_detections(image, self.boxes, self.scores, class_names, colors,
                               self.class_ids, mask_alpha)

    def get_input_details(self):
        model_inputs = self.session.get_inputs()
        self.input_names = [model_inputs[i].name for i in range(len(model_inputs))]

        self.input_shape = model_inputs[0].shape
        self.input_height = self.input_shape[2]
        self.input_width = self.input_shape[3]

    def get_output_details(self):
        model_outputs = self.session.get_outputs()
        self.output_names = [model_outputs[i].name for i in range(len(model_outputs))]

