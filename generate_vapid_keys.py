from py_vapid import Vapid
from cryptography.hazmat.primitives import serialization
import base64

# Tạo đối tượng Vapid
vapid = Vapid()

# Tạo cặp khóa
vapid.generate_keys()

# Lấy public key và private key dưới dạng PEM, sau đó chuyển sang base64
public_key = vapid.public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)
private_key = vapid.private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Chuyển đổi sang base64 và loại bỏ header/footer
public_key_b64 = base64.urlsafe_b64encode(public_key).decode('utf-8').strip('=')
private_key_b64 = base64.urlsafe_b64encode(private_key).decode('utf-8').strip('=')

# In ra màn hình
print("VAPID Public Key:", public_key_b64)
print("VAPID Private Key:", private_key_b64)

# Tùy chọn: Lưu vào file
vapid.save_key('vapid_private.pem')  # Lưu private key
vapid.save_public_key('vapid_public.pem')  # Lưu public key