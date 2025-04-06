const functions = require('firebase-functions');
const cors = require('cors')({ origin: true });
const { exec } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const express = require('express');
const app = express();
const multer = require('multer');
const upload = multer({ dest: os.tmpdir() });

// Use CORS middleware
app.use(cors);

// Create a route for the check-plagiarism endpoint
app.post('/check-plagiarism', upload.array('files'), (req, res) => {
  const files = req.files;
  const language = req.body.language || 'python';
  
  if (!files || files.length === 0) {
    return res.status(400).json({ error: 'No files provided' });
  }
  
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'moss-'));
  const savedFilenames = [];
  
  try {
    // Save the uploaded files to the temporary directory
    for (const file of files) {
      const filePath = path.join(tempDir, file.originalname);
      fs.copyFileSync(file.path, filePath);
      savedFilenames.push(file.originalname);
    }
    
    // Path to the moss.pl script
    const mossPath = path.join(__dirname, 'moss.pl');
    
    // Make the moss.pl script executable
    fs.chmodSync(mossPath, '755');
    
    // Prepare the MOSS command
    const mossCmd = [
      '/usr/bin/perl',
      mossPath,
      '-l', language,
      '-m', '10'
    ].concat(savedFilenames.map(filename => path.join(tempDir, filename)));
    
    // Execute the MOSS command
    exec(mossCmd.join(' '), { cwd: tempDir }, (error, stdout, stderr) => {
      if (error) {
        console.error(`Error executing MOSS: ${error.message}`);
        return res.status(500).json({ 
          error: 'MOSS execution failed',
          details: stderr,
          command: mossCmd.join(' '),
          output: stdout
        });
      }
      
      // Extract the MOSS URL from the output
      const lines = stdout.trim().split('\n');
      const mossUrl = lines[lines.length - 1];
      
      if (mossUrl.startsWith('http://moss.stanford.edu/results/')) {
        // Fetch and parse the MOSS results
        fetchAndParseMossResults(mossUrl)
          .then(results => {
            res.json({ url: mossUrl, results: results });
          })
          .catch(error => {
            console.error(`Error parsing MOSS results: ${error.message}`);
            res.json({ url: mossUrl, results: null });
          });
      } else {
        res.status(500).json({ 
          error: 'Invalid output received from MOSS',
          full_output: stdout
        });
      }
    });
  } catch (error) {
    console.error(`Unexpected error: ${error.message}`);
    res.status(500).json({ error: `An internal server error occurred: ${error.message}` });
  } finally {
    // Clean up temporary files
    for (const file of files) {
      if (fs.existsSync(file.path)) {
        fs.unlinkSync(file.path);
      }
    }
  }
});

// Function to fetch and parse MOSS results
function fetchAndParseMossResults(mossUrl) {
  const axios = require('axios');
  const cheerio = require('cheerio');
  
  return axios.get(mossUrl)
    .then(response => {
      const $ = cheerio.load(response.data);
      const results = [];
      
      // Parse the MOSS results table
      $('table tr').each((i, row) => {
        if (i === 0) return; // Skip header row
        
        const cols = $(row).find('td');
        if (cols.length >= 3) {
          const file1 = $(cols[0]).text().trim();
          const file2 = $(cols[1]).text().trim();
          const linesMatched = $(cols[2]).text().trim();
          
          // Extract file names and percentages
          const match1 = file1.match(/^(.*)\s+\((\d+)%\)$/);
          const match2 = file2.match(/^(.*)\s+\((\d+)%\)$/);
          
          if (match1 && match2) {
            results.push({
              file1: {
                name: match1[1],
                percentage: parseInt(match1[2])
              },
              file2: {
                name: match2[1],
                percentage: parseInt(match2[2])
              },
              lines_matched: parseInt(linesMatched) || 0,
              comparison_url: $(cols[0]).find('a').attr('href')
            });
          }
        }
      });
      
      return results;
    });
}

// Create another route for fetch-comparison
app.post('/fetch-comparison', (req, res) => {
  const { url } = req.body;
  
  if (!url) {
    return res.status(400).json({ error: 'No comparison URL provided' });
  }
  
  const axios = require('axios');
  const cheerio = require('cheerio');
  
  axios.get(url)
    .then(response => {
      const $ = cheerio.load(response.data);
      
      // Extract file names from title
      const title = $('title').text().trim();
      const match = title.match(/Matches for (.*) and (.*)/);
      const file1Name = match ? match[1].trim() : 'File 1';
      const file2Name = match ? match[2].trim() : 'File 2';
      
      res.json({
        file1: {
          name: file1Name,
          code: []
        },
        file2: {
          name: file2Name,
          code: []
        },
        sourceUrl: url
      });
    })
    .catch(error => {
      console.error(`Error fetching comparison: ${error.message}`);
      res.status(500).json({
        error: `Failed to fetch: ${error.message}`,
        file1: {
          name: 'Error',
          code: []
        },
        file2: {
          name: 'Error',
          code: []
        },
        sourceUrl: url
      });
    });
});

// Export the express app as a Firebase Function
exports.api = functions.https.onRequest(app);
