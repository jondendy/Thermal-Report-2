#!/usr/bin/env python3
"""
Simplified FLIR Thermal Image Processor
Uses flirimageextractor library for easy processing
"""

import numpy as np
import os
import glob
from pathlib import Path

try:
    from flirimageextractor import FlirImageExtractor
except ImportError as e:  # raise instead of exit(1) so a web server doesn't die at import
    FlirImageExtractor = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


def get_colorblind_friendly_thermal_cmap():
    """
    Create a colorblind-friendly thermal colormap.
    Cold (black/blue) -> warm (red/orange/yellow) -> hot (white)
    Blue tones only in the cold range, ensuring no blue between yellow and white.
    """
    # Imported here: the original (legacy-stable) version used this name without importing it.
    from matplotlib.colors import LinearSegmentedColormap
    colors = [
        (0.0, 0.0, 0.0),      # Black (coldest)
        (0.0, 0.0, 0.5),      # Dark blue
        (0.0, 0.0, 0.8),      # Blue
        (0.4, 0.0, 0.2),      # Dark red/purple
        (0.7, 0.0, 0.0),      # Dark red
        (1.0, 0.0, 0.0),      # Red
        (1.0, 0.5, 0.0),      # Orange
        (1.0, 0.8, 0.0),      # Yellow
        (1.0, 1.0, 0.5),      # Light yellow
        (1.0, 1.0, 1.0),      # White (hottest)
    ]

    return LinearSegmentedColormap.from_list('thermal_colorblind', colors, N=256)



class SimpleFLIRProcessor:
    """
    Simple wrapper for processing FLIR thermal images using flirimageextractor
    """

    def __init__(self):
        """Initialize the FLIR processor"""
        if FlirImageExtractor is None:
            raise RuntimeError(
                "flirimageextractor is not installed ({}). Run: pip install "
                "'flirimageextractor==1.3.0' and install exiftool (apt install "
                "libimage-exiftool-perl).".format(_IMPORT_ERROR))
        self.extractor = FlirImageExtractor()
        print("FLIR Image Processor initialized")

    def process_single_image(self, image_path, display=True):
        """
        Process a single FLIR image

        Args:
            image_path (str): Path to FLIR image
            display (bool): Whether to display the plot

        Returns:
            tuple: (temperature_array, statistics_dict)
        """
        print(f"\nProcessing: {image_path}")
        print("-" * 60)

        # Check file exists
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"File not found: {image_path}")

        # Process the image
        self.extractor.process_image(image_path)

        # Get temperature data as numpy array (in Celsius)
        temp_data = self.extractor.get_thermal_np()

        # Get RGB image
        rgb_data = self.extractor.get_rgb_np()

        # Calculate statistics
        stats = self.calculate_statistics(temp_data)

        # Print statistics
        print("\nTemperature Statistics:")
        print(f"  Min:     {stats['min']:.2f} °C")
        print(f"  Max:     {stats['max']:.2f} °C")
        print(f"  Mean:    {stats['mean']:.2f} °C")
        print(f"  Median:  {stats['median']:.2f} °C")
        print(f"  Std Dev: {stats['std']:.2f} °C")
        print(f"  Shape:   {temp_data.shape}")

        # Display if requested
        if display:
            try:
                import matplotlib.pyplot as plt

                fig, axes = plt.subplots(1, 2, figsize=(14, 6))

                # Visual image
                axes[0].imshow(rgb_data)
                axes[0].set_title('Visual Image', fontsize=14)
                axes[0].axis('off')

                # Thermal image
                im = axes[1].imshow(temp_data, cmap=get_colorblind_friendly_thermal_cmap(), interpolation='nearest')
                axes[1].set_title(f'Thermal Image\n{stats["min"]:.1f}°C - {stats["max"]:.1f}°C', fontsize=14)
                axes[1].axis('off')
                cbar = plt.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04)
                cbar.set_label('Temperature (°C)', fontsize=12)

                plt.tight_layout()
                plt.show()

            except ImportError:
                print("\nNote: Install matplotlib for visualization: pip install matplotlib")

        return temp_data, stats

    def calculate_statistics(self, temp_data):
        """
        Calculate temperature statistics

        Args:
            temp_data (numpy.ndarray): Temperature data

        Returns:
            dict: Statistics
        """
        # Remove any NaN or infinite values
        valid_data = temp_data[np.isfinite(temp_data)]

        if len(valid_data) == 0:
            return {
                'min': np.nan, 'max': np.nan, 'mean': np.nan,
                'median': np.nan, 'std': np.nan
            }

        return {
            'min': float(np.min(valid_data)),
            'max': float(np.max(valid_data)),
            'mean': float(np.mean(valid_data)),
            'median': float(np.median(valid_data)),
            'std': float(np.std(valid_data))
        }

    def process_folder(self, folder_path, pattern='*.jpg', save_csv=None):
        """
        Process all FLIR images in a folder

        Args:
            folder_path (str): Path to folder
            pattern (str): File pattern (default: '*.jpg')
            save_csv (str): Optional CSV filename to save results

        Returns:
            dict: Results for each image
        """
        folder = Path(folder_path)

        if not folder.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")

        # Find all matching files
        image_files = list(folder.glob(pattern))
        image_files.extend(folder.glob(pattern.upper()))  # Also check uppercase
        image_files = list(set(image_files))  # Remove duplicates

        if not image_files:
            print(f"No images found matching pattern '{pattern}' in {folder_path}")
            return {}

        print(f"\nFound {len(image_files)} images to process")
        print("=" * 60)

        results = {}
        all_stats = []

        for img_file in sorted(image_files):
            try:
                # Process without display
                temp_data, stats = self.process_single_image(str(img_file), display=False)

                results[img_file.name] = {
                    'temperature_data': temp_data,
                    'statistics': stats
                }

                # Collect stats for CSV
                stats_row = {'filename': img_file.name}
                stats_row.update(stats)
                all_stats.append(stats_row)

            except Exception as e:
                print(f"ERROR processing {img_file.name}: {e}")
                continue

        # Save to CSV if requested
        if save_csv and all_stats:
            import csv
            with open(save_csv, 'w', newline='') as f:
                fieldnames = ['filename', 'min', 'max', 'mean', 'median', 'std']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_stats)
            print(f"\nResults saved to: {save_csv}")

        print(f"\nSuccessfully processed {len(results)} images")
        return results

    def save_temperature_array(self, temp_data, output_file):
        """
        Save temperature array to file

        Args:
            temp_data (numpy.ndarray): Temperature data
            output_file (str): Output filename (.npy or .csv)
        """
        ext = os.path.splitext(output_file)[1].lower()

        if ext == '.npy':
            np.save(output_file, temp_data)
            print(f"Saved temperature array to {output_file}")
        elif ext == '.csv':
            np.savetxt(output_file, temp_data, delimiter=',', fmt='%.2f')
            print(f"Saved temperature CSV to {output_file}")
        else:
            raise ValueError("Output file must be .npy or .csv")

    def export_thermal_image(self, image_path, output_path, colormap='jet'):
        """
        Export thermal image with colormap

        Args:
            image_path (str): Input FLIR image
            output_path (str): Output image path
            colormap (str): Matplotlib colormap name
        """
        self.extractor.process_image(image_path)

        # Save with custom colormap
        try:
            from matplotlib import cm
            import matplotlib

            # Get the colormap
            cmap = getattr(cm, colormap, cm.jet)

            # Process and save
            self.extractor.save_images()
            print(f"Thermal image exported")

        except Exception as e:
            print(f"Error exporting: {e}")


# Example usage functions

def example_single_image():
    """Example: Process a single image"""
    processor = SimpleFLIRProcessor()

    # Replace with your image path
    image_path = "thermal_image.jpg"

    # Process and display
    temp_data, stats = processor.process_single_image(image_path, display=True)

    # Save temperature data
    processor.save_temperature_array(temp_data, "temperatures.csv")

    print(f"\nTemperature array shape: {temp_data.shape}")
    print(f"Data type: {temp_data.dtype}")


def example_batch_processing():
    """Example: Process multiple images in a folder"""
    processor = SimpleFLIRProcessor()

    # Replace with your folder path
    folder_path = "./thermal_images"

    # Process all JPG files
    results = processor.process_folder(
        folder_path,
        pattern="*.jpg",
        save_csv="thermal_summary.csv"
    )

    # Access individual results
    for filename, data in results.items():
        print(f"\n{filename}:")
        print(f"  Temperature range: {data['statistics']['min']:.1f} - {data['statistics']['max']:.1f} °C")


def example_error_handling():
    """Example: Process images with error handling"""
    processor = SimpleFLIRProcessor()

    image_paths = ["image1.jpg", "image2.jpg", "image3.jpg"]

    for img_path in image_paths:
        try:
            temp_data, stats = processor.process_single_image(img_path, display=False)
            print(f"✓ {img_path}: {stats['mean']:.1f}°C average")

        except FileNotFoundError:
            print(f"✗ {img_path}: File not found")
        except Exception as e:
            print(f"✗ {img_path}: Error - {e}")


if __name__ == "__main__":
    print("FLIR Thermal Image Processor")
    print("=" * 60)
    print("\nThis script provides functions to process FLIR thermal images.")
    print("\nUncomment one of the example functions below to test:")
    print("  - example_single_image()")
    print("  - example_batch_processing()")
    print("  - example_error_handling()")
    print("\nOr import this module and use SimpleFLIRProcessor class directly.")

    # Uncomment to run an example:
    example_single_image()
    # example_batch_processing()
    # example_error_handling()
