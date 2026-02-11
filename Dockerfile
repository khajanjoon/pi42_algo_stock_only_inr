# Use an official Python runtime as a parent image
FROM python:3.12-slim
# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# Run the bot
CMD ["python3", "run.py"]
