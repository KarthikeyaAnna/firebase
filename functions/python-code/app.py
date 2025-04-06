from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import subprocess
import tempfile
from dotenv import load_dotenv
import logging
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import html

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
CORS(app)

# Get MOSS path from environment or use default
MOSS_PATH = os.getenv('MOSS_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moss.pl'))
# MOSS_USER_ID = os.getenv('MOSS_USER_ID') # User ID is embedded in the script

if not os.path.exists(MOSS_PATH):
    logger.error(f"MOSS script not found at {MOSS_PATH}")
    raise FileNotFoundError(f"MOSS script not found at {MOSS_PATH}. Please download the moss.pl script from https://theory.stanford.edu/~aiken/moss/ and place it in your project directory.")
# if not MOSS_USER_ID:
#     logger.error("MOSS_USER_ID not found in environment variables")
#     raise ValueError("MOSS_USER_ID not found in environment variables. Please add it to your .env file.")

def fetch_and_parse_moss_results(moss_url):
    logger.info(f"Attempting to fetch and parse MOSS summary from: {moss_url}")
    results_data = []
    session = requests.Session()

    try:
        # 1. Fetch main page
        main_response = session.get(moss_url if moss_url.endswith('/') else moss_url + '/', timeout=20)
        main_response.raise_for_status()
        main_soup = BeautifulSoup(main_response.text, 'lxml')
        base_url = main_response.url # Base URL after redirects

        # 2. Check for "No matches" on the MAIN page FIRST
        page_text = main_soup.get_text(separator=" ", strip=True)
        if "No matches were found" in page_text:
            logger.info("MOSS reported 'No matches were found' on main results page.")
            return [] # Return empty list

        # 3. Look for the summary TABLE directly on the MAIN page
        summary_table = main_soup.find('table')

        if not summary_table:
            # If no table on main page, log HTML and fail for now
            logger.error("No summary table found directly on the main MOSS results page.")
            logger.debug(f"Main results page HTML (first 500 chars):\n{main_soup.prettify()[:500]}")
            # We previously tried navigating frames, but maybe that was wrong for the summary.
            # Let's stop here if the main page doesn't have the table directly.
            return None # Indicate parsing failure

        # 4. Parse the summary_table found on the main page
        logger.debug("Found summary table on main results page. Parsing rows...")
        rows = summary_table.find_all('tr')
        if len(rows) <= 1:
            logger.warning(f"Summary table found, but has only {len(rows)} rows. Treating as no matches.")
            return []

        # --- Table Row Parsing (Use the logic that expects filename + percentage) ---
        for row in rows[1:]:
            cols = row.find_all('td')
            if len(cols) >= 3:
                file1_link = cols[0].find('a')
                file2_link = cols[1].find('a')
                lines_matched_text = cols[2].text.strip()

                if file1_link and file2_link:
                    # Use text from the parent TD as it might contain percentage outside link
                    file1_text = cols[0].text.strip()
                    file2_text = cols[1].text.strip()

                    logger.debug(f"Parsing row - Col1 Text: '{file1_text}', Col2 Text: '{file2_text}', Lines: '{lines_matched_text}'")

                    match1 = re.match(r'^(.*)\s+\((\d+)%\)$', file1_text)
                    match2 = re.match(r'^(.*)\s+\((\d+)%\)$', file2_text)

                    if match1 and match2:
                         file1_name = match1.group(1)
                         file1_percent = int(match1.group(2))
                         file2_name = match2.group(1)
                         file2_percent = int(match2.group(2))

                         # Link for detailed comparison is usually on file1's link
                         comparison_href = file1_link.get('href')
                         comparison_url = urljoin(base_url, comparison_href) if comparison_href else None

                         results_data.append({
                            'file1': {'name': file1_name, 'percentage': file1_percent},
                            'file2': {'name': file2_name, 'percentage': file2_percent},
                            'lines_matched': int(lines_matched_text) if lines_matched_text.isdigit() else 0,
                            'comparison_url': comparison_url
                         })
                    else:
                         logger.warning(f"Could not parse file names/percentages from TD text: '{file1_text}' | '{file2_text}'")
                         # Optionally, try extracting just names if regex fails completely?
                         # file1_name = file1_link.text.strip() # Less reliable
                         # file2_name = file2_link.text.strip() # Less reliable

                else: logger.warning(f"Could not find links in table row columns: {cols}")
            else: logger.warning(f"Row has less than 3 columns: {row}")
        # --- End Table Row Parsing ---

        logger.info(f"Finished parsing summary table. Found {len(results_data)} matches.")
        return results_data

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching MOSS results URL ({moss_url}): {e}")
        return None
    except Exception as e:
        logger.exception(f"Error parsing MOSS results from {moss_url}: {e}")
        return None

@app.route('/check-plagiarism', methods=['POST'])
def check_plagiarism():
    try:
        files = request.files.getlist('files')
        language = request.form.get('language', 'python')

        logger.info(f"Received {len(files)} files for language: {language}")

        if not files:
            return jsonify({'error': 'No files provided'}), 400

        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            saved_filenames = []
            for file in files:
                filename = file.filename
                file_path = os.path.join(temp_dir, filename)

                file.save(file_path)
                if not os.path.exists(file_path):
                    logger.error(f"File {file_path} was not saved successfully")
                    return jsonify({'error': f'Failed to save file {filename}'}), 500

                with open(file_path, 'r') as f:
                    content = f.read()
                    if not content.strip():
                        logger.error(f"File {file_path} is empty")
                        return jsonify({'error': f'File {filename} is empty'}), 500
                    logger.debug(f"File {filename} saved at {file_path}. First 100 chars:\n{content[:100]}")

                saved_filenames.append(filename)

            try:
                dir_contents = os.listdir(temp_dir)
                logger.debug(f"Contents of temp directory {temp_dir}: {dir_contents}")
            except Exception as e:
                 logger.error(f"Could not list contents of {temp_dir}: {e}")

            if not saved_filenames:
                 logger.error("No files were successfully saved to the temporary directory.")
                 return jsonify({'error': 'Failed to process any uploaded files.'}), 500

            # Prepare MOSS command WITHOUT -d flag
            moss_cmd = [
                '/usr/bin/perl',
                MOSS_PATH,
                '-l', language,
                '-m', '10'
            ]
            moss_cmd.extend(saved_filenames)

            logger.debug(f"Executing command: {' '.join(moss_cmd)} in CWD: {temp_dir}")

            result = subprocess.run(moss_cmd, capture_output=True, text=True, cwd=temp_dir)

            logger.debug(f"MOSS return code: {result.returncode}")
            logger.debug(f"MOSS stdout: {result.stdout}")
            logger.debug(f"MOSS stderr: {result.stderr}")

            if result.returncode == 0:
                # Extract URL from the last non-empty line of stdout
                lines = result.stdout.strip().splitlines()
                original_moss_url = lines[-1] if lines else ""

                logger.info(f"Raw MOSS output (last line): {original_moss_url}")

                if original_moss_url.startswith('http://moss.stanford.edu/results/'):
                    logger.info(f"Processed MOSS URL: {original_moss_url}")

                    # Fetch and parse results
                    parsed_data = fetch_and_parse_moss_results(original_moss_url)

                    # Return both the original URL and the parsed data (if any)
                    return jsonify({
                        'url': original_moss_url,
                        'results': parsed_data # This will be null if parsing failed
                    })
                else:
                    logger.error(f"Could not extract valid URL from last line of MOSS output: {original_moss_url}")
                    error_detail = result.stderr or "No specific error message from MOSS stderr."
                    # Include full stdout in error if URL extraction fails
                    return jsonify({'error': f'Invalid output received from MOSS. Check logs. Details: {error_detail}', 'full_output': result.stdout}), 500
            else:
                error_msg = f"MOSS execution failed with return code {result.returncode}\nCommand: {' '.join(moss_cmd)}\nError: {result.stderr}\nOutput: {result.stdout}"
                logger.error(error_msg)
                return jsonify({
                    'error': f"MOSS execution failed. Check server logs. Details: {result.stderr}",
                    'details': result.stderr,
                    'command': ' '.join(moss_cmd),
                    'output': result.stdout
                }), 500

    except Exception as e:
        logger.exception("An unexpected error occurred in /check-plagiarism")
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500

@app.route('/fetch-comparison', methods=['POST'])
def fetch_comparison():
    try:
        data = request.json
        comparison_url = data.get('url')
        
        if not comparison_url:
            return jsonify({'error': 'No comparison URL provided'}), 400
            
        logger.info(f"Fetching MOSS comparison from: {comparison_url}")
        
        # Fetch the comparison page
        session = requests.Session()
        response = session.get(comparison_url, timeout=20)
        response.raise_for_status()
        
        # Parse the frameset HTML
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Initialize variables
        file1_name = "File 1"
        file2_name = "File 2"
        
        # Extract file names from title
        title = soup.find('title')
        if title:
            title_text = title.text.strip()
            match = re.search(r'Matches for (.*) and (.*)', title_text)
            if match:
                file1_name = match.group(1).strip()
                file2_name = match.group(2).strip()
                logger.info(f"Found files: {file1_name} and {file2_name}")
        
        # Find all frame sources
        frames = soup.find_all('frame')
        
        # Collect frame URLs by name
        frame_urls = {}
        for frame in frames:
            name = frame.get('name', '')
            src = frame.get('src', '')
            if name and src:
                frame_urls[name] = src
                logger.debug(f"Found frame: {name} -> {src}")
        
        # Return the data without extracting code
        return jsonify({
            'file1': {
                'name': file1_name,
                'code': []  # No code extraction
            },
            'file2': {
                'name': file2_name,
                'code': []  # No code extraction
            },
            'sourceUrl': comparison_url
        })
                
    except Exception as e:
        logger.exception(f"Error fetching comparison: {str(e)}")
        return jsonify({
            'error': f'Failed to fetch: {str(e)}',
            'file1': {
                'name': 'Error',
                'code': []
            },
            'file2': {
                'name': 'Error',
                'code': []
            },
            'sourceUrl': comparison_url
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 
