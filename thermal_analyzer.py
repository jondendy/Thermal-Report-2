#!/usr/bin/env python3
"""
Thermal Analyzer - Hot Spot Detection and Report Generation
Builds on SimpleFLIRProcessor to add intelligence layer for building surveys
"""
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import json

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("PIL not installed. Install with: pip install Pillow")
    Image = ImageDraw = ImageFont = None


# Pixels colder than this are treated as sky reflection / invalid and ignored.
SKY_REFLECTION_CUTOFF_C = -45.0


class HotSpot:
    """Represents a detected thermal anomaly"""
    
    def __init__(self, location: Tuple[int, int], temperature: float, 
                 area_size: int, severity: str, excess: float = None):
        self.excess = excess  # degC above the building baseline (None for the legacy detector)
        self.location = location  # (row, col) in temperature array
        self.temperature = temperature
        self.area_size = area_size  # number of pixels in hot spot
        self.severity = severity  # 'low', 'medium', 'high', 'critical'
        self.description = ""
        self.likely_cause = ""
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'location': self.location,
            'temperature': float(self.temperature),
            'area_size': int(self.area_size),
            'severity': self.severity,
            'excess': None if self.excess is None else float(self.excess),
            'description': self.description,
            'likely_cause': self.likely_cause
        }


class ThermalAnalyzer:
    """
    Advanced thermal analysis with hot spot detection and reporting
    
    This analyzer adds intelligence to SimpleFLIRProcessor:
    - Detects thermal anomalies (hot spots)
    - Labels and annotates images
    - Generates narrative reports for building surveys
    """
    
    def __init__(self, base_temp_threshold=None, sensitivity='medium'):
        """
        Initialize thermal analyzer
        
        Args:
            base_temp_threshold: Base temperature for absolute detection (None for auto)
            sensitivity: 'low', 'medium', or 'high' - affects detection threshold
        """
        self.base_threshold = base_temp_threshold
        self.sensitivity = sensitivity
        
        # Sensitivity multipliers for statistical detection
        self.sensitivity_map = {
            'low': 3.0,    # 3 std devs above mean
            'medium': 2.0,  # 2 std devs above mean
            'high': 1.5     # 1.5 std devs above mean
        }
    
    def detect_hot_spots(self, temp_data: np.ndarray, 
                        method='statistical',
                        threshold=None) -> List[HotSpot]:
        """
        Detect hot spots in thermal image data
        
        Args:
            temp_data: 2D numpy array of temperature values
            method: 'statistical' (auto), 'absolute' (fixed temp), 'relative' (local)
            threshold: Optional override threshold
        
        Returns:
            List of detected HotSpot objects
        """
        
        # Filter out sky reflection artifacts (typically < -45°C) and keep only finite values
        # The mask is APPLIED here: excluded pixels become NaN so they no longer
        # distort the mean / std used by the detectors (the original code computed
        # the mask but then passed the unfiltered array on).
        temp_data = np.asarray(temp_data, dtype=float)
        valid_mask = np.isfinite(temp_data) & (temp_data > SKY_REFLECTION_CUTOFF_C)
        if not valid_mask.any():
            return []
        temp_data = np.where(valid_mask, temp_data, np.nan)

        if method == 'statistical':
            return self._detect_statistical(temp_data, threshold)
        elif method == 'absolute':
            return self._detect_absolute(temp_data, threshold)
        elif method == 'relative':
            return self._detect_relative(temp_data)
        else:
            raise ValueError(f"Unknown method: {method}")
    
    def _detect_statistical(self, temp_data: np.ndarray, 
                           threshold=None) -> List[HotSpot]:
        """Statistical hot spot detection using mean + std deviation"""
        valid_data = temp_data[np.isfinite(temp_data)]
        mean_temp = np.mean(valid_data)
        std_temp = np.std(valid_data)
        
        # Use sensitivity or custom threshold
        multiplier = threshold if threshold else self.sensitivity_map[self.sensitivity]
        hot_threshold = mean_temp + (multiplier * std_temp)
        
        print(f"Detection: mean={mean_temp:.1f}°C, std={std_temp:.1f}°C")
        print(f"Hot spot threshold: {hot_threshold:.1f}°C")
        
        # Find pixels above threshold
        with np.errstate(invalid='ignore'):
            hot_mask = temp_data > hot_threshold
        
        return self._extract_hot_spots(temp_data, hot_mask, 'statistical')
    
    def _detect_absolute(self, temp_data: np.ndarray, 
                        threshold=None) -> List[HotSpot]:
        """Absolute temperature threshold detection"""
        thresh = threshold if threshold else self.base_threshold
        if thresh is None:
            thresh = 30.0  # Default 30°C
        
        print(f"Absolute threshold: {thresh}°C")
        with np.errstate(invalid='ignore'):
            hot_mask = temp_data > thresh
        
        return self._extract_hot_spots(temp_data, hot_mask, 'absolute')
    
    def _detect_relative(self, temp_data: np.ndarray) -> List[HotSpot]:
        """Local relative detection - finds local maxima (hot spots warmer than neighbors)"""
        from scipy.ndimage import maximum_filter
        
        # Use maximum filter to find local peaks
        # Each pixel is compared to neighborhood (e.g., 9x9 window)
        neighborhood_size = 9
        filled = np.where(np.isfinite(temp_data), temp_data, np.nanmin(temp_data))
        local_max = maximum_filter(filled, size=neighborhood_size)
        
        # A pixel is a local maximum if it equals the max in its neighborhood
        # and is significantly warmer than the mean
        valid_data = temp_data[np.isfinite(temp_data)]
        mean_temp = np.mean(valid_data)
        std_temp = np.std(valid_data)
        
        # Local peak must be warmer than local neighborhood AND above mean
        with np.errstate(invalid='ignore'):
            is_local_max = (filled == local_max) & (temp_data > mean_temp + std_temp)
        
        # Remove edges and create mask
        margin = neighborhood_size // 2
        is_local_max[:margin, :] = False
        is_local_max[-margin:, :] = False
        is_local_max[:, :margin] = False
        is_local_max[:, -margin:] = False
        
        print(f"Relative detection: found {np.sum(is_local_max)} local maxima")
        
        return self._extract_hot_spots(temp_data, is_local_max, 'relative')
    
    def _extract_hot_spots(self, temp_data: np.ndarray, 
                          hot_mask: np.ndarray,
                          method_type: str) -> List[HotSpot]:
        """Extract connected components from hot spot mask"""
        from scipy import ndimage
        
        # Label connected regions
        labeled_array, num_features = ndimage.label(hot_mask)
        
        hot_spots = []
        
        for label in range(1, num_features + 1):
            # Get pixels for this hot spot
            region = labeled_array == label
            region_temps = temp_data[region]
            region_size = np.sum(region)
            
            # Skip very small regions (noise)
            if region_size < 3:
                continue
            
            # Find hottest point in region (centroid)
            max_temp = np.max(region_temps)
            max_loc = np.where((region) & (temp_data == max_temp))
            location = (int(max_loc[0][0]), int(max_loc[1][0]))
            
            # Determine severity
            severity = self._classify_severity(max_temp, temp_data)
            
            hot_spot = HotSpot(
                location=location,
                temperature=max_temp,
                area_size=int(region_size),
                severity=severity
            )
            
            hot_spots.append(hot_spot)
        
        print(f"Found {len(hot_spots)} hot spots using {method_type} method")
        return hot_spots
    
    def _classify_severity(self, temp: float, temp_data: np.ndarray) -> str:
        """Classify hot spot severity based on temperature"""
        valid_data = temp_data[np.isfinite(temp_data)]
        mean = np.mean(valid_data)
        std = np.std(valid_data)
        
        delta = temp - mean
        
        if delta > 3 * std:
            return 'critical'
        elif delta > 2 * std:
            return 'high'
        elif delta > std:
            return 'medium'
        else:
            return 'low'
    
    def label_hot_spots(self, image_path: str, hot_spots: List[HotSpot], 
                       output_path: str = None) -> Image:
        """
        Create annotated thermal image with hot spot markers and labels
        
        Args:
            image_path: Path to original thermal image
            hot_spots: List of detected HotSpot objects
            output_path: Optional path to save labeled image
        
        Returns:
            PIL Image with annotations
        """
        if Image is None:
            raise ImportError("PIL not available for image labeling")
        
        # Load image
        img = Image.open(image_path).convert('RGB')
        draw = ImageDraw.Draw(img)
        
        # Try to load a font, fallback to default
        try:
            font = ImageFont.truetype("arial.ttf", 16)
            small_font = ImageFont.truetype("arial.ttf", 12)
        except:
            font = ImageFont.load_default()
            small_font = font
        
        # Color map for severity
        severity_colors = {
            'low': '#FFFF00',      # Yellow
            'medium': '#FFA500',   # Orange
            'high': '#FF4500',     # Orange-Red
            'critical': '#FF0000'  # Red
        }
        
        # Calculate scaling between thermal array and visual image
        # Assume typical FLIR scaling: 120x160 sensor -> 480x640 image
        scale_y = img.height / 120  # 480 / 120 = 4.0
        scale_x = img.width / 160   # 640 / 160 = 4.0

        # Label each hot spot
        for idx, spot in enumerate(hot_spots, 1):
            row, col = spot.location
            # Scale coordinates to match image dimensions
            col = int(col * scale_x)
            row = int(row * scale_y)
            color = severity_colors.get(spot.severity, '#FF0000')
            
            # Draw crosshair marker
            marker_size = 10
            draw.line([(col-marker_size, row), (col+marker_size, row)], 
                     fill=color, width=2)
            draw.line([(col, row-marker_size), (col, row+marker_size)], 
                     fill=color, width=2)
            
            # Draw circle around hot spot
            radius = int(np.sqrt(spot.area_size) * 2)
            draw.ellipse([(col-radius, row-radius), (col+radius, row+radius)],
                        outline=color, width=2)
            
            # Add temperature label
            label = f"#{idx}: {spot.temperature:.1f}°C"
            # Position label above spot
            label_pos = (col + 15, row - 10)
            
            # Draw background for text
            bbox = draw.textbbox(label_pos, label, font=font)
            draw.rectangle(bbox, fill='black')
            draw.text(label_pos, label, fill=color, font=font)
            
            # Add severity indicator
            severity_label = f"[{spot.severity.upper()}]"
            severity_pos = (col + 15, row + 5)
            draw.text(severity_pos, severity_label, fill=color, font=small_font)
        
        # Add summary box
        summary_text = f"Hot Spots Detected: {len(hot_spots)}"
        summary_pos = (10, 10)
        summary_bbox = draw.textbbox(summary_pos, summary_text, font=font)
        draw.rectangle([(5, 5), (summary_bbox[2]+5, summary_bbox[3]+5)], 
                      fill='black', outline='white', width=2)
        draw.text(summary_pos, summary_text, fill='white', font=font)
        
        # Save if output path provided
        if output_path:
            img.save(output_path)
            print(f"Labeled image saved to: {output_path}")
        
        return img
    
    def assess_hot_spot_cause(self, hot_spot: HotSpot, 
                             image_context: Dict = None) -> str:
        """
        Interpret what the hot spot likely indicates for building surveys
        
        Args:
            hot_spot: HotSpot object to assess
            image_context: Optional context (location in building, etc.)
        
        Returns:
            String describing likely cause
        """
        causes = []
        
        # Assess based on severity
        if hot_spot.severity == 'critical':
            causes.append("Significant thermal anomaly detected")
            if hot_spot.area_size > 100:
                causes.append("Large area suggests major insulation failure or active heat source")
            else:
                causes.append("Localized hot spot may indicate electrical issue or severe air leak")
        
        elif hot_spot.severity == 'high':
            causes.append("Notable temperature elevation")
            if hot_spot.area_size > 50:
                causes.append("Possible insulation gap or thermal bridging")
            else:
                causes.append("May indicate air infiltration point or small heat source")
        
        elif hot_spot.severity == 'medium':
            causes.append("Moderate thermal anomaly")
            causes.append("Could be minor insulation issue or normal building variation")
        
        else:  # low
            causes.append("Minor temperature variation")
            causes.append("Likely within normal building thermal patterns")
        
        # Add context-based assessment
        if image_context:
            location = image_context.get('location', '').lower()
            if 'wall' in location:
                causes.append("Wall location: check for stud thermal bridging or cavity issues")
            elif 'ceiling' in location:
                causes.append("Ceiling location: inspect attic insulation above this area")
            elif 'window' in location or 'door' in location:
                causes.append("Opening location: verify weather sealing and frame insulation")
        
        return ". ".join(causes) + "."
    
    def generate_report(self, image_name: str, hot_spots: List[HotSpot],
                       stats: Dict, image_context: Dict = None) -> str:
        """
        Generate narrative HTML report describing thermal findings
        
        Args:
            image_name: Name of thermal image
            hot_spots: List of detected hot spots
            stats: Dictionary with temperature statistics
            image_context: Optional context information
        
        Returns:
            HTML formatted report string
        """
        
        # Sort hot spots by severity and temperature
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        sorted_spots = sorted(hot_spots, 
                            key=lambda x: (severity_order[x.severity], -x.temperature))
        
        # Build HTML report
        html = []
        html.append('<div class="thermal-report">')
        html.append(f'<h2>Thermal Analysis Report: {image_name}</h2>')
        html.append(f'<p class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>')
        
        # Executive Summary
        html.append('<div class="summary">')
        html.append('<h3>Executive Summary</h3>')
        html.append(f'<p><strong>Total Hot Spots Detected:</strong> {len(hot_spots)}</p>')
        
        # Count by severity
        severity_counts = {}
        for spot in hot_spots:
            severity_counts[spot.severity] = severity_counts.get(spot.severity, 0) + 1

        return '\n'.join(html)
