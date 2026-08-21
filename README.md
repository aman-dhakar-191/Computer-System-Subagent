To run the agent.py on Windows, follow these steps:

1. Open Command Prompt or PowerShell and navigate to the directory where you've saved the files:
   ```
   cd C:\Users\Aman\Documents\Computer-System-Subagent
   ```

2. Install the required dependencies from requirements.txt:
   ```
   pip install -r requirements.txt
   ```

3. Run the agent.py file:
   ```
   python agent.py
   ```

   This will start the FastAPI server on port 8765.

4. To test it, you can open a web browser and go to http://localhost:8765/docs to see the API documentation and test the endpoints.

**Important Notes:**
- Make sure you're using Python 3.7 or higher
- The server will be accessible at http://localhost:8765
- Keep the command prompt open while the server is running
- To stop the server, press Ctrl+C in the command prompt
- If you encounter any issues with permissions, try running the command prompt as Administrator
- If you want to run the server on a different port, modify the uvicorn command in agent.py to use a different port number, e.g., `uvicorn.run(app, host="0.0.0.0", port=8000)`
- The application will automatically reload on code changes when run with `python agent.py --reload` (you can add this flag for development)
- When making HTTP requests to the API, you'll need to send a JSON payload like:
  ```json
  {
    "action": "get_cpu",
    "params": {}
  }
  ```

Let me know if you'd like me to explain any of these steps in more detail.