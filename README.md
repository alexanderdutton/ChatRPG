# Gemini NPC Dialogue Game

This project demonstrates a simple, scalable interactive game where Google's Gemini API provides the dialogue for Non-Player Characters (NPCs). It's designed to help you understand how to integrate Large Language Models (LLMs) into applications and manage conversational state.

## Architecture Overview

The application follows a client-server architecture:

*   **Frontend (Client):** A simple HTML/CSS/JavaScript web page that allows users to type messages and displays NPC responses. It communicates with the backend via HTTP requests.
*   **Backend (Server):** Built with Python using FastAPI, this server handles:
    *   Receiving user input.
    *   Managing game session state (including conversation history).
    *   Interacting with the Gemini API to generate NPC dialogue.
    *   Sending the generated dialogue back to the frontend.

## Project Structure

```
gemini-npc-game/
├── backend/
│   ├── main.py                 # FastAPI application entry point
│   ├── gemini_service.py       # Module for Gemini API interaction
│   ├── game_state_manager.py   # Manages in-memory game session state
│   └── requirements.txt        # Python dependencies
├── frontend/
│   └── index.html              # Simple HTML/JS client
└── README.md                   # This file
```

## Setup and Running

### 1. Obtain a Gemini API Key

You'll need an API key for the Gemini API. Follow the instructions here to get one.

### 2. Set up the Backend

1.  **Navigate to the `backend` directory:**
    ```bash
    cd backend
    ```
2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows: `venv\Scripts\activate`
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Set your Gemini API Key as an environment variable:**
    ```bash
    export GEMINI_API_KEY="YOUR_API_KEY_HERE" # On Windows (CMD): `set GEMINI_API_KEY="YOUR_API_KEY_HERE"`
    # Or for PowerShell: `$env:GEMINI_API_KEY="YOUR_API_KEY_HERE"`
    ```
    **Important:** Never hardcode your API key directly in your code.

5.  **Run the FastAPI server:**
    ```bash
    uvicorn main:app --reload
    ```
    The backend server will typically run on `http://127.0.0.1:8000`.

### 3. Run the Frontend

1.  **Open the `frontend/index.html` file in your web browser.**
    You can simply double-click the file, or open it via your browser's file menu.

### 4. Interact!

Now you can type messages into the input box on the web page and press "Send" (or Enter) to interact with the Gemini-powered NPC.

## Key Concepts for Skill Improvement

*   **API Integration:** Understanding how to make requests to external services (Gemini API).
*   **Asynchronous Programming:** FastAPI and `google.generativeai` use `async`/`await` for non-blocking I/O, which is crucial for scalable web applications.
*   **State Management:** The `GameStateManager` demonstrates how to maintain context across multiple user interactions. For a production app, this would typically involve a database.
*   **Prompt Engineering:** The quality of NPC dialogue heavily depends on how you craft your prompts to the LLM. Experiment with different initial prompts and instructions.
*   **Environment Variables:** Securely managing sensitive information like API keys.
*   **Modular Design:** Separating concerns into different files (e.g., `gemini_service.py`, `game_state_manager.py`) makes the codebase more organized and maintainable.
*   **Error Handling:** Basic error handling is included, but robust error handling is vital for production systems.

## Scalability Considerations

*   **Database for State:** The current `GameStateManager` is in-memory. For multiple users and persistent sessions, integrate a database (e.g., PostgreSQL, MongoDB, Redis).
*   **Load Balancing:** For high traffic, deploy multiple instances of the backend behind a load balancer.
*   **Containerization (Docker):** Package your backend application in a Docker container for easier deployment and scaling on platforms like Kubernetes.
*   **API Rate Limits:** Be mindful of Gemini API rate limits. Implement retry mechanisms or queuing if necessary.
*   **Caching:** Cache frequently accessed data or common Gemini responses if applicable to reduce API calls.

## Next Steps / Enhancements

*   **More Sophisticated Game State:** Add more game elements like inventory, character stats, location changes, quests.
*   **Advanced Prompt Engineering:** Experiment with system prompts, few-shot examples, and function calling to give the NPC more specific behaviors or access to game data.
*   **User Authentication:** Implement user login to personalize game experiences.
*   **WebSockets:** For real-time, bidirectional communication between client and server, which can enhance the interactive feel.
*   **Deployment:** Deploy the application to a cloud platform (e.g., Google Cloud Run, App Engine, AWS EC2/ECS).