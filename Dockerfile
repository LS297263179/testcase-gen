FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

COPY . .

RUN mkdir -p output data

EXPOSE 5000

# 生产环境使用 gunicorn，开发环境用 start.py
CMD ["gunicorn", "-c", "gunicorn.conf.py", "web:app"]
