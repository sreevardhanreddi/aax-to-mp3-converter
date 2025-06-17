import subprocess
import re
import os
import json
import sys
from pathlib import Path


class AAXProcessor:
    def __init__(self, tables_path="."):
        """
        Initialize AAX processor.

        Args:
            tables_path (str): Path to the directory containing rainbow tables (*.rtc files)
        """
        self.tables_path = Path(tables_path)
        self.rcrack_binary = self.find_rcrack_binary()

    def find_rcrack_binary(self):
        """Find the rcrack binary executable."""
        # Check common locations
        possible_paths = [
            "./rcrack",
            "./run/rcrack",
            "rcrack",
            "/app/audible_rainbow_tables/rcrack",
        ]

        for path in possible_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        # Check if rcrack is in PATH
        try:
            subprocess.run(["rcrack", "--help"], capture_output=True, check=False)
            return "rcrack"
        except FileNotFoundError:
            pass

        raise FileNotFoundError(
            "rcrack binary not found. Please ensure it's installed or in the correct path."
        )

    def extract_sha1_checksum(self, aax_file):
        """
        Extract SHA1 checksum from AAX file using ffprobe.

        Args:
            aax_file (str): Path to the AAX file

        Returns:
            str: SHA1 checksum if found, None otherwise
        """
        try:
            # Run ffprobe to get file information
            result = subprocess.run(
                ["ffprobe", aax_file],
                capture_output=True,
                text=True,
                # stderr=subprocess.STDOUT,
            )

            output = result.stdout + result.stderr

            # Search for the checksum pattern
            checksum_pattern = r"\[aax\] file checksum == ([a-fA-F0-9]+)"
            match = re.search(checksum_pattern, output)

            if match:
                checksum = match.group(1)
                print(f"Found SHA1 checksum: {checksum}")
                return checksum
            else:
                print("No checksum found in ffprobe output")
                print("Output:", output)
                return None

        except FileNotFoundError:
            raise FileNotFoundError("ffprobe not found. Please install FFmpeg.")
        except subprocess.CalledProcessError as e:
            print(f"Error running ffprobe: {e}")
            return None

    def recover_activation_bytes(self, checksum):
        """
        Recover activation bytes using rainbow tables.

        Args:
            checksum (str): SHA1 checksum to crack

        Returns:
            str: Activation bytes if found, None otherwise
        """
        try:
            print(f"Attempting to crack checksum: {checksum}")
            print(f"Using rainbow tables in: {self.tables_path}")

            orginal_dir = os.getcwd()
            os.chdir(self.tables_path)
            # Run rcrack with the checksum
            result = subprocess.run(
                [self.rcrack_binary, str(self.tables_path), "-h", checksum],
                capture_output=True,
                text=True,
            )
            print(" ".join(result.args))

            output = result.stdout + result.stderr
            print("RainbowCrack output:")
            print(output)

            # Parse the output to find activation bytes
            # Look for patterns like "plaintext of [hash] is [bytes]"
            activation_pattern = r"hex:([a-fA-F0-9]+)"
            match = re.search(activation_pattern, output)

            if match:
                activation_bytes = match.group(1)
                print(f"Successfully recovered activation bytes: {activation_bytes}")
                return activation_bytes
            else:
                print("Could not find activation bytes in output")
                return None

        except subprocess.CalledProcessError as e:
            print(f"Error running rcrack: {e}")
            return None
        finally:
            os.chdir(orginal_dir)

    def process_aax_file(self, aax_file):
        """
        Complete process: extract checksum and recover activation bytes.

        Args:
            aax_file (str): Path to the AAX file

        Returns:
            dict: Dictionary containing checksum and activation_bytes
        """
        if not os.path.isfile(aax_file):
            raise FileNotFoundError(f"AAX file not found: {aax_file}")

        print(f"Processing AAX file: {aax_file}")

        # Step 1: Extract SHA1 checksum
        checksum = self.extract_sha1_checksum(aax_file)

        if not checksum:
            return {"error": "Could not extract SHA1 checksum"}

        with open("activation_bytes.json", "r+") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

            if checksum in data:
                print(f"Activation bytes already found for checksum: {checksum}")
                return {"checksum": checksum, "activation_bytes": data[checksum]}
            else:
                print(f"Activation bytes not found for checksum: {checksum}")

        # Step 2: Recover activation bytes
        activation_bytes = self.recover_activation_bytes(checksum)
        if not activation_bytes:
            return {"checksum": checksum, "error": "Could not recover activation bytes"}

        # if activation bytes is found store it in a json file
        with open("activation_bytes.json", "w+") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

            data[checksum] = activation_bytes
            json.dump(data, f, indent=4)

        return {"checksum": checksum, "activation_bytes": activation_bytes}
