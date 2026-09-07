export interface CrmCustomer {
  id: number
  name: string
  email: string
  age: number | null
  aadhar_number: string | null
  address: string | null
  phone: string | null
  created_at: string
}

export interface CrmLoginResponse {
  customer: CrmCustomer
  created: boolean
}