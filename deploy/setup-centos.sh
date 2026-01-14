#!/bin/bash
# CentOS/RHEL setup script for Faderbank
# Run as root or with sudo

set -e

echo "=== Faderbank CentOS Setup ==="

# Install system dependencies
echo "Installing system packages..."
dnf install -y python3 python3-pip python3-devel gcc

# Create service user
echo "Creating faderbank user..."
if ! id "faderbank" &>/dev/null; then
    useradd -r -s /sbin/nologin faderbank
fi

# Create directories
echo "Creating directories..."
mkdir -p /opt/faderbank
mkdir -p /var/log/faderbank
chown faderbank:faderbank /var/log/faderbank

# Copy application files (adjust source path as needed)
echo "Copying application files..."
# cp -r /path/to/faderbank/* /opt/faderbank/
# Or: git clone <repo> /opt/faderbank

# Create virtual environment
echo "Setting up Python virtual environment..."
cd /opt/faderbank
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Set ownership
chown -R faderbank:faderbank /opt/faderbank

# Install systemd service
echo "Installing systemd service..."
cp deploy/gunicorn.service /etc/systemd/system/faderbank.service
systemctl daemon-reload
systemctl enable faderbank

# Configure SELinux (if enabled)
if command -v getenforce &> /dev/null && [ "$(getenforce)" != "Disabled" ]; then
    echo "Configuring SELinux..."
    setsebool -P httpd_can_network_connect 1
fi

# Open firewall (if firewalld is running)
if systemctl is-active --quiet firewalld; then
    echo "Configuring firewall..."
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy your app files to /opt/faderbank/"
echo "  2. Create config/zebby.py with your APP_SECRET_KEY"
echo "  3. Configure Apache (see deploy/apache-faderbank.conf)"
echo "  4. Start the service: systemctl start faderbank"
echo "  5. Check status: systemctl status faderbank"
echo "  6. View logs: journalctl -u faderbank -f"
echo ""
