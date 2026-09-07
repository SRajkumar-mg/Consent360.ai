export interface Job {
  id: number
  title: string
  company: string
  location: string
  type: string
  salary: string
  tags: string[]
  posted: string
}

export const JOBS: Job[] = [
  {
    id: 1,
    title: 'Senior Frontend Engineer',
    company: 'TechNova Solutions',
    location: 'Bangalore',
    type: 'Full-time',
    salary: '₹18–28 LPA',
    tags: ['React', 'TypeScript', 'Remote'],
    posted: '2 days ago',
  },
  {
    id: 2,
    title: 'Backend Developer',
    company: 'CloudSync Labs',
    location: 'Hyderabad',
    type: 'Full-time',
    salary: '₹15–22 LPA',
    tags: ['Python', 'FastAPI', 'PostgreSQL'],
    posted: '5 days ago',
  },
  {
    id: 3,
    title: 'Product Manager',
    company: 'InnoVista',
    location: 'Mumbai',
    type: 'Full-time',
    salary: '₹22–35 LPA',
    tags: ['Strategy', 'Agile', 'Analytics'],
    posted: '1 day ago',
  },
  {
    id: 4,
    title: 'UX Designer',
    company: 'PixelCraft Studio',
    location: 'Chennai',
    type: 'Contract',
    salary: '₹12–18 LPA',
    tags: ['Figma', 'UI/UX', 'Design Systems'],
    posted: '3 days ago',
  },
  {
    id: 5,
    title: 'DevOps Engineer',
    company: 'InfraEdge',
    location: 'Pune',
    type: 'Full-time',
    salary: '₹16–25 LPA',
    tags: ['AWS', 'Kubernetes', 'CI/CD'],
    posted: '1 week ago',
  },
  {
    id: 6,
    title: 'Data Scientist',
    company: 'DataPulse AI',
    location: 'Bangalore',
    type: 'Full-time',
    salary: '₹20–32 LPA',
    tags: ['Python', 'ML', 'TensorFlow'],
    posted: '4 days ago',
  },
  {
    id: 7,
    title: 'Mobile App Developer',
    company: 'AppVerse',
    location: 'Remote',
    type: 'Remote',
    salary: '₹14–22 LPA',
    tags: ['React Native', 'Flutter', 'iOS'],
    posted: '6 days ago',
  },
  {
    id: 8,
    title: 'Security Analyst',
    company: 'CyberShield Corp',
    location: 'Delhi NCR',
    type: 'Full-time',
    salary: '₹18–28 LPA',
    tags: ['SOC', 'Penetration Testing', 'SIEM'],
    posted: '2 days ago',
  },
  {
    id: 9,
    title: 'Technical Writer',
    company: 'DocuFlow',
    location: 'Remote',
    type: 'Part-time',
    salary: '₹8–14 LPA',
    tags: ['API Docs', 'Markdown', 'Git'],
    posted: '1 day ago',
  },
]
