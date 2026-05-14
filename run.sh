#!/bin/bash
# Start both servers in parallel

# Backend
(
  cd backend
  source venv/bin/activate
  uvicorn main:app --reload --port 8000
) &
BACKEND_PID=$!

# Frontend
(
  cd frontend
  npm run dev
) &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID" INT TERM
wait
