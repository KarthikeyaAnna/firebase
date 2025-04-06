const functions = require('firebase-functions');
const { spawn } = require('child_process');
const cors = require('cors')({origin: true});
const express = require('express');
const app = express();

// Use CORS middleware
app.use(cors);

// Set up route to proxy to your Python app
app.all('*', (req, res) => {
  // Launch the Python process
  const process = spawn('python', ['python-code/app.py'], {
    env: { ...process.env, PORT: '5001' }
  });
  
  let stdoutData = '';
  let stderrData = '';
  
  // Collect stdout data
  process.stdout.on('data', (data) => {
    stdoutData += data.toString();
  });
  
  // Collect stderr data
  process.stderr.on('data', (data) => {
    stderrData += data.toString();
    console.error(`Python stderr: ${data}`);
  });
  
  // Handle process completion
  process.on('close', (code) => {
    if (code !== 0) {
      console.error(`Python process exited with code ${code}`);
      return res.status(500).send(`Error: Python process exited with code ${code}\n${stderrData}`);
    }
    
    // Try to parse the output as JSON
    try {
      const responseData = JSON.parse(stdoutData);
      return res.json(responseData);
    } catch (e) {
      return res.send(stdoutData);
    }
  });
  
  // Handle process errors
  process.on('error', (err) => {
    console.error(`Failed to start Python process: ${err}`);
    return res.status(500).send(`Failed to start Python process: ${err}`);
  });
});

// Export the Express app as a Firebase Function
exports.api = functions.https.onRequest(app);
