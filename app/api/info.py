from fastapi import APIRouter
from app.config import settings

router=APIRouter(prefix='/api/v1',tags=['API Guide'])

@router.get('',include_in_schema=False)
async def api_guide():
    base=settings.public_base_url.rstrip('/') if settings.public_base_url else 'https://YOUR_DOMAIN'
    return {
        'name':'Teleshop Reseller API','version':'2.0',
        'base_url':base+'/api/v1',
        'authentication':{'header':'X-Reseller-Key','format':'sb_...','note':'Generate/rotate a key from the Telegram bot API menu.'},
        'endpoints':{
            'list_products':{'method':'GET','path':'/products','example':f"curl -H 'X-Reseller-Key: sb_YOUR_KEY' {base}/api/v1/products"},
            'product':{'method':'GET','path':'/products/{product_id}','example':f"curl -H 'X-Reseller-Key: sb_YOUR_KEY' {base}/api/v1/products/PRODUCT_ID"},
            'create_order':{'method':'POST','path':'/orders?product_id=PRODUCT_ID','example':f"curl -X POST -H 'X-Reseller-Key: sb_YOUR_KEY' '{base}/api/v1/orders?product_id=PRODUCT_ID'"},
            'order_status':{'method':'GET','path':'/orders/{order_id}','example':f"curl -H 'X-Reseller-Key: sb_YOUR_KEY' {base}/api/v1/orders/ORDER_ID"},
        },
        'openapi_docs':base+'/docs',
    }
